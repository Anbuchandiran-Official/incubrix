import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from incubrix_editor.renderer import probe, render
from incubrix_editor.timeline import Timeline, Word


def test_real_ffmpeg_render_keeps_audio_video_and_source_in_sync(tmp_path):
    ffmpeg = os.getenv("INCUBRIX_FFMPEG", "ffmpeg")
    ffprobe = os.getenv("INCUBRIX_FFPROBE", "ffprobe")
    if not shutil.which(ffmpeg) or not shutil.which(ffprobe):
        pytest.skip("FFmpeg and FFprobe are required for the real-media integration test")
    source, output = tmp_path / "source.mp4", tmp_path / "render.mp4"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=4",
                    "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=4",
                    "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
                   check=True, capture_output=True)
    timeline = Timeline(4, [Word("mute", "mute", 2.2, 2.4), Word("beep", "beep", 3.2, 3.4)])
    timeline.add_caption_at("Coming soon", 1.1, 1.8)
    timeline.cut(0, 1)
    timeline.set_speed(1, 2, 2)
    timeline.reorder(3, 4, 0)
    timeline.add_word_effect("mute", ["mute"])
    timeline.add_word_effect("bleep", ["beep"])
    source_hash = probe(source)
    import hashlib
    original_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    result = render(source, output, timeline)
    streams = json.loads(subprocess.run([ffprobe, "-v", "error", "-show_entries",
                                         "stream=codec_type,duration", "-of", "json", str(output)],
                                        check=True, capture_output=True, text=True).stdout)["streams"]
    durations = [float(stream["duration"]) for stream in streams
                 if stream.get("codec_type") in {"audio", "video"} and stream.get("duration")]
    tolerance = 1 / max(source_hash["fps"], 1)
    assert len(durations) == 2
    assert abs(durations[0] - durations[1]) <= tolerance
    assert abs(probe(output)["duration_s"] - timeline.duration) <= tolerance
    assert result["source_sha256"] == original_digest
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_digest
