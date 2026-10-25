from hashlib import sha256
from tempfile import SpooledTemporaryFile

from starlette.datastructures import Headers, UploadFile

import incubrix_editor.api as api


def test_video_upload_is_saved_under_source_hash_and_edits_do_not_change_it(tmp_path, monkeypatch):
    monkeypatch.setenv("INCUBRIX_DATA_DIR", str(tmp_path))
    original = b"unchanged source fixture bytes"
    monkeypatch.setattr(api, "probe_media", lambda path: {
        "duration_s": 12.0, "fps": 30.0, "has_audio": True, "width": 640, "height": 360
    })
    stream = SpooledTemporaryFile()
    stream.write(original)
    stream.seek(0)
    upload = UploadFile(filename="original.mp4", file=stream,
                        headers=Headers({"content-type": "video/mp4"}))
    response = api.create_session_from_upload(upload, 12.0, "[]", "upload-test-key")
    session_id = response["session_id"]
    source = api._source_files[session_id]
    expected_hash = sha256(original).hexdigest()
    assert response["source_sha256"] == expected_hash
    assert source.name == f"{expected_hash}.mp4"
    api._sessions[session_id].cut(0, 3)
    assert sha256(source.read_bytes()).hexdigest() == expected_hash
