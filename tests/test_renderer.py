import json

from incubrix_editor import renderer
from incubrix_editor.timeline import Timeline, Word


def test_renderer_builds_source_safe_cut_speed_caption_mute_and_bleep_graph(tmp_path, monkeypatch):
    source = tmp_path / "original.mp4"
    source.write_bytes(b"immutable")
    output = tmp_path / "render.mp4"
    timeline = Timeline(4, [Word("b", "bleep", 2.2, 2.4), Word("m", "mute", 3.2, 3.4)])
    timeline.add_caption_at("Go", 1.1, 1.8)
    timeline.cut(0, 1)
    timeline.set_speed(1, 2, 2)
    timeline.add_word_effect("bleep", ["b"])
    timeline.add_word_effect("mute", ["m"])
    source_probe = {"duration_s": 4.0, "fps": 30.0, "has_audio": True, "width": 640, "height": 360}
    output_probe = {**source_probe, "duration_s": timeline.duration}
    monkeypatch.setattr(renderer, "probe", lambda path: source_probe if path == source else output_probe)
    commands = []

    def fake_run(command):
        commands.append(command)
        if command[0] == "ffmpeg":
            output.write_bytes(b"rendered")
            return ""
        return json.dumps({"streams": [
            {"codec_type": "video", "duration": str(timeline.duration)},
            {"codec_type": "audio", "duration": str(timeline.duration)}
        ]})

    monkeypatch.setattr(renderer, "_run", fake_run)
    result = renderer.render(source, output, timeline)
    graph = commands[0][commands[0].index("-filter_complex") + 1]
    assert "concat=n=2:v=1:a=1" in graph
    assert "atempo=2.000000000" in graph
    assert "drawtext=" in graph and "aevalsrc=" in graph and "volume=0" in graph
    assert result["source_sha256"] == renderer._sha256(source)
    assert result["duration_s"] == timeline.duration
