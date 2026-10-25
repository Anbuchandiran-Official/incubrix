"""FFmpeg renderer for source-backed timelines; the source is read-only."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

from .timeline import Timeline


class RenderError(RuntimeError):
    pass


def _ffmpeg_binary() -> str:
    """Find FFmpeg from configuration, PATH, or the packaged imageio binary."""
    configured = os.getenv("INCUBRIX_FFMPEG")
    if configured:
        if Path(configured).is_file():
            return configured
        configured_on_path = shutil.which(configured)
        if configured_on_path:
            return configured_on_path
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError) as exc:
        raise RenderError("FFmpeg is unavailable. Install project dependencies or set INCUBRIX_FFMPEG.") from exc


def _run(command: list[str]) -> str:
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=900)
        return result.stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise RenderError(f"Media processing failed: {detail[-1600:]}") from exc


def _run_with_stderr(command: list[str], *, check: bool = True) -> tuple[str, str]:
    try:
        result = subprocess.run(command, check=check, capture_output=True, text=True, timeout=900)
        return result.stdout, result.stderr
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise RenderError(f"Media processing failed: {detail[-1600:]}") from exc


def probe(path: Path) -> dict[str, Any]:
    configured_probe = os.getenv("INCUBRIX_FFPROBE")
    binary = (configured_probe if configured_probe and Path(configured_probe).is_file()
              else shutil.which(configured_probe) if configured_probe
              else None) or shutil.which("ffprobe")
    try:
        if not binary:
            raise RenderError("FFprobe was not found; using FFmpeg metadata fallback")
        data = json.loads(_run([binary, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]))
    except (RenderError, json.JSONDecodeError):
        ffmpeg = _ffmpeg_binary()
        _, details = _run_with_stderr([ffmpeg, "-hide_banner", "-i", str(path)], check=False)
        duration = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", details)
        video = re.search(r"Video:\s*([^\r\n]*?)(?=\r?\n|$)", details)
        size = re.search(r"(\d{2,5})x(\d{2,5})", video.group(1)) if video else None
        fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", video.group(1)) if video else None
        if not duration or not video or not size:
            raise RenderError("FFmpeg could not read the video stream metadata")
        audio = re.search(r"Audio:\s*([^\r\n]*)", details)
        sample_rate = re.search(r"(\d+)\s*Hz", audio.group(1)) if audio else None
        return {"duration_s": int(duration.group(1)) * 3600 + int(duration.group(2)) * 60 + float(duration.group(3)),
                "fps": float(fps_match.group(1)) if fps_match else 30.0,
                "has_audio": bool(audio), "audio_sample_rate": int(sample_rate.group(1)) if sample_rate else 48000,
                "width": int(size.group(1)), "height": int(size.group(2))}
    video = next((stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"), None)
    if video is None:
        raise RenderError("The source file has no video stream")
    rate = video.get("avg_frame_rate", "30/1").split("/")
    fps = float(rate[0]) / float(rate[1]) if len(rate) == 2 and float(rate[1]) else 30.0
    audio_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    return {"duration_s": float(data.get("format", {}).get("duration", video.get("duration", 0))),
            "fps": fps, "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
            "audio_sample_rate": int(audio_stream.get("sample_rate", 48000)) if audio_stream else 48000,
            "width": int(video.get("width", 0)), "height": int(video.get("height", 0))}


def render(source: Path, output: Path, timeline: Timeline) -> dict[str, Any]:
    """Render the exact segment order and effects to a new file, then probe it."""
    metadata = probe(source)
    if not timeline.segments:
        raise RenderError("Cannot render an empty timeline")
    output.parent.mkdir(parents=True, exist_ok=True)
    args = [_ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source)]
    filters: list[str] = []
    video_labels: list[str] = []
    audio_labels: list[str] = []
    current_offset = 0.0
    active_captions: list[tuple[float, float, str, str]] = []
    for index, segment in enumerate(timeline.segments):
        start, end, speed = segment.source_start, segment.source_end, segment.speed
        duration = segment.duration
        filters.append(f"[0:v]trim=start={start:.9f}:end={end:.9f},setpts=(PTS-STARTPTS)/{speed:.9f}[v{index}]")
        video_labels.append(f"[v{index}]")
        caption_video_label = f"v{index}"
        if metadata["has_audio"]:
            # Some screen recordings end their audio stream before the video.
            # Pad with silence and trim to the segment's exact timeline length
            # so that this source-level gap does not desynchronize the render.
            filters.append(f"[0:a]atrim=start={start:.9f}:end={end:.9f},asetpts=PTS-STARTPTS{_atempo(speed)},apad=whole_dur={duration:.9f},atrim=duration={duration:.9f}[a{index}]")
            audio_label = f"a{index}"
            mute_enables: list[str] = []
            beep_enables: list[str] = []
            for annotation in timeline.annotations:
                left, right = max(start, annotation.start), min(end, annotation.end)
                if left >= right or annotation.kind not in {"mute", "bleep"}:
                    continue
                local_start = (left - start) / speed
                local_end = (right - start) / speed
                interval = f"between(t,{local_start:.9f},{local_end:.9f})"
                if annotation.kind == "mute":
                    mute_enables.append(interval)
                else:
                    beep_enables.append(interval)
            if mute_enables:
                filters.append(f"[{audio_label}]volume=0:enable='{'+'.join(mute_enables)}'[am{index}]")
                audio_label = f"am{index}"
            if beep_enables:
                tone = f"tone{index}"
                beep_condition = "+".join(beep_enables)
                filters.append(f"aevalsrc=0.18*sin(2*PI*880*t):s=48000:d={duration:.9f},volume=0:enable='not({beep_condition})'[{tone}]")
                filters.append(f"[{audio_label}][{tone}]amix=inputs=2:duration=first:normalize=0[ab{index}]")
                audio_label = f"ab{index}"
            audio_labels.append(f"[{audio_label}]")
        for annotation in timeline.annotations:
            if annotation.kind != "caption":
                continue
            left, right = max(start, annotation.start), min(end, annotation.end)
            if left >= right:
                continue
            text_file = output.parent / f".{output.stem}-caption-{len(active_captions)}.txt"
            text_file.write_text(annotation.text or "", encoding="utf-8")
            color = str(annotation.style.get("color", "white"))
            if color not in {"red", "blue", "green", "white", "yellow", "black"}:
                color = "white"
            next_label = f"vc{index}_{len(active_captions)}"
            font = os.getenv("INCUBRIX_FONT")
            if not font:
                candidates = [Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("C:/Windows/Fonts/arial.ttf")]
                font = next((str(candidate) for candidate in candidates if candidate.is_file()), None)
            font_option = f":fontfile='{_filter_path(Path(font))}'" if font else ""
            filters.append(f"[{caption_video_label}]drawtext=textfile='{_filter_path(text_file)}'{font_option}:fontcolor={color}:fontsize=h/18:x=(w-text_w)/2:y=h-text_h-40:enable='between(t,{(left-start)/speed:.9f},{(right-start)/speed:.9f})'[{next_label}]")
            caption_video_label = next_label
            video_labels[-1] = f"[{next_label}]"
            active_captions.append((current_offset + (left - start) / speed,
                                    current_offset + (right - start) / speed,
                                    str(text_file), annotation.text or ""))
        current_offset += duration
    if metadata["has_audio"]:
        interleaved = "".join(video_labels[i] + audio_labels[i] for i in range(len(video_labels)))
        filters.append(interleaved +
                       f"concat=n={len(timeline.segments)}:v=1:a=1[vout][aout]")
    else:
        filters.append("".join(video_labels) + f"concat=n={len(timeline.segments)}:v=1:a=0[vout]")
    args += ["-filter_complex", ";".join(filters), "-map", "[vout]"]
    if metadata["has_audio"]:
        args += ["-map", "[aout]"]
    args += ["-t", f"{timeline.duration:.9f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]
    if metadata["has_audio"]:
        args += ["-c:a", "aac", "-b:a", "192k"]
    args += ["-movflags", "+faststart", str(output)]
    try:
        _run(args)
        rendered = probe(output)
    finally:
        for _, _, text_file, _ in active_captions:
            Path(text_file).unlink(missing_ok=True)
    tolerance = 1.0 / max(metadata["fps"], 1.0)
    if abs(rendered["duration_s"] - timeline.duration) > tolerance + 0.001:
        raise RenderError(f"Rendered duration {rendered['duration_s']:.6f}s differs from timeline {timeline.duration:.6f}s by more than one frame")
    if metadata["has_audio"]:
        ffprobe = os.getenv("INCUBRIX_FFPROBE") or shutil.which("ffprobe")
        try:
            if not ffprobe:
                raise RenderError("FFprobe not found; measuring stream ends with FFmpeg")
            streams = json.loads(_run([ffprobe, "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", str(output)]))["streams"]
            lengths = [float(s["duration"]) for s in streams if s.get("codec_type") in {"video", "audio"} and s.get("duration")]
            if len(lengths) == 2 and abs(lengths[0] - lengths[1]) > tolerance + 0.001:
                raise RenderError("Rendered audio and video differ by more than one frame")
        except (RenderError, json.JSONDecodeError):
            video_end, audio_end = _measure_stream_ends(output, metadata["fps"], metadata["audio_sample_rate"])
            if abs(video_end - audio_end) > tolerance + 0.001:
                raise RenderError("Rendered audio and video differ by more than one frame")
    return {**rendered, "timeline_duration_s": round(timeline.duration, 9), "tolerance_frames": 1,
            "source_sha256": _sha256(source), "output_sha256": _sha256(output)}


def _atempo(speed: float) -> str:
    filters: list[float] = []
    while speed > 2:
        filters.append(2.0)
        speed /= 2
    while speed < 0.5:
        filters.append(0.5)
        speed /= 0.5
    filters.append(speed)
    return "," + ",".join(f"atempo={value:.9f}" for value in filters)


def _filter_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def _sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _measure_stream_ends(path: Path, fps: float, sample_rate: int) -> tuple[float, float]:
    ffmpeg = _ffmpeg_binary()
    _, video_log = _run_with_stderr([ffmpeg, "-hide_banner", "-i", str(path), "-map", "0:v:0",
                                     "-vf", "showinfo", "-f", "null", "NUL"])
    _, audio_log = _run_with_stderr([ffmpeg, "-hide_banner", "-i", str(path), "-map", "0:a:0",
                                     "-af", "ashowinfo", "-f", "null", "NUL"])
    video_pts = [float(value) for value in re.findall(r"pts_time:([0-9.]+)", video_log)]
    audio_packets = [(float(pts), int(count)) for pts, count in
                     re.findall(r"pts_time:([0-9.]+).*?nb_samples:(\d+)", audio_log)]
    if not video_pts or not audio_packets:
        raise RenderError("FFmpeg could not measure the rendered audio/video endpoints")
    frame_step = video_pts[-1] - video_pts[-2] if len(video_pts) > 1 else 1 / max(fps, 1)
    video_end = video_pts[-1] + (frame_step if frame_step > 0 else 1 / max(fps, 1))
    audio_end = audio_packets[-1][0] + audio_packets[-1][1] / max(sample_rate, 1)
    return video_end, audio_end
