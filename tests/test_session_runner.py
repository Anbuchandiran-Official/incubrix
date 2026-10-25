from incubrix_editor.session_runner import run_session


def test_session_runner_records_each_step_and_hash():
    result = run_session({"source_duration_s": 30, "steps": [
        {"operation": "cut", "start_s": 0, "end_s": 5},
        {"operation": "undo"}
    ]})
    assert [row["status"] for row in result["results"]] == ["applied", "applied"]
    assert result["results"][0]["timeline_hash"] != result["results"][1]["timeline_hash"]
    assert result["final_timeline"]["duration_s"] == 30


def test_runner_resolves_clarification_across_steps():
    result = run_session({"source_duration_s": 60,
                          "words": [{"id": "p1", "text": "pricing", "start": 2, "end": 2.2},
                                    {"id": "p2", "text": "pricing", "start": 20, "end": 20.3}],
                          "steps": [{"say": "Remove the part about pricing"},
                                    {"answer": "the second one"}]})
    assert result["results"][0]["status"] == "needs_clarification"
    assert result["results"][1]["status"] == "applied"
    assert result["final_timeline"]["duration_s"] == 59.7


def test_same_script_has_same_hash_on_three_replays():
    payload = {"source_duration_s": 30, "steps": [
        {"operation": "cut", "start_s": 0, "end_s": 5},
        {"operation": "speed", "start_s": 10, "end_s": 20, "speed": 2},
        {"operation": "undo"}, {"operation": "redo"}
    ]}
    runs = [run_session(payload) for _ in range(3)]
    hashes = [[row["timeline_hash"] for row in result["results"]] for result in runs]
    assert hashes[0] == hashes[1] == hashes[2]
