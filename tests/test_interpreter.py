from incubrix_editor.interpreter import apply_interpretation, interpret, resolve_clarification
from incubrix_editor.timeline import Timeline, Word


def make_timeline():
    return Timeline(120, [Word("a", "pricing", 10, 10.3), Word("b", "comes", 10.3, 10.6),
                          Word("c", "later", 10.6, 11), Word("d", "pricing", 40, 40.4)])


def test_ambiguous_phrase_requests_choice():
    result = interpret("Remove the part about pricing", make_timeline())
    assert result.status == "needs_clarification"
    assert len(result.options) == 2
    selected = resolve_clarification(result, "the second one")
    assert selected.status == "applied"
    assert selected.operation["start_s"] == 40


def test_time_cut_has_explicit_original_time_operation():
    result = interpret("Cut from 1:00 to 1:20", make_timeline())
    assert result.status == "applied"
    assert result.operation["start_s"] == 60
    assert result.operation["end_s"] == 80


def test_cut_last_seconds_uses_original_source_duration():
    timeline = Timeline(13)
    result = apply_interpretation(interpret("Cut the last 3 seconds", timeline), timeline)
    assert result["status"] == "applied"
    assert result["operations"][0]["start_s"] == 10
    assert result["operations"][0]["end_s"] == 13
    assert timeline.duration == 10


def test_keep_and_reorder_instructions_are_structured_deterministically():
    timeline = Timeline(100)
    keep = interpret("Keep from 00:10 to 00:50", timeline)
    assert keep.operation == {"operation": "keep", "start_s": 10, "end_s": 50}
    apply_interpretation(keep, timeline)
    reorder = interpret("Move the part from 00:20 to 00:30 to 00:00", timeline)
    assert reorder.operation == {"operation": "reorder", "start_s": 20, "end_s": 30, "destination_s": 0}
    assert apply_interpretation(reorder, timeline)["status"] == "applied"
    assert timeline.original_to_current(25) == 5


def test_caption_anchors_to_matching_words():
    timeline = make_timeline()
    result = interpret("Add a caption 'Coming soon' when I say pricing comes later", timeline)
    response = apply_interpretation(result, timeline)
    assert response["status"] == "applied"
    assert timeline.annotations[0].word_ids == ["a", "b", "c"]


def test_caption_at_timestamp_is_time_anchored():
    timeline = make_timeline()
    result = interpret("Add caption ‘Coming soon’ at 00:12.", timeline)
    response = apply_interpretation(result, timeline)
    assert response["status"] == "applied"
    annotation = timeline.annotations[0]
    assert annotation.text == "Coming soon"
    assert annotation.start == 12
    assert annotation.end == 15
    assert annotation.word_ids == []


def test_caption_accepts_explicit_time_range_in_common_phrasings():
    for instruction in (
        "Add a caption 'Welcome to vjsidhu vlogs' from 00:03 to 00:07",
        "Add a caption 'Welcome to vjsidhu vlogs' at 00:03 to 00:07",
        "Add a caption 'Welcome to vjsidhu vlogs' between 00:03 and 00:07",
    ):
        timeline = make_timeline()
        response = apply_interpretation(interpret(instruction, timeline), timeline)
        assert response["status"] == "applied", instruction
        annotation = timeline.annotations[0]
        assert (annotation.start, annotation.end, annotation.text) == (3, 7, "Welcome to vjsidhu vlogs")


def test_speed_instruction_uses_original_time_and_says_so():
    timeline = make_timeline()
    response = apply_interpretation(interpret("Speed up from 1:00 to 1:30 by 2x", timeline), timeline)
    assert response["status"] == "applied"
    assert response["time_policy"] == "original source time for all instructions"
    assert timeline.duration == 105


def test_caption_can_be_recolored_as_one_undoable_edit():
    timeline = make_timeline()
    apply_interpretation(interpret("Add a caption 'Coming soon' when I say pricing comes later", timeline), timeline)
    result = interpret("Make that caption red", timeline)
    assert result.operation["operation"] == "update_caption"
    apply_interpretation(result, timeline)
    assert timeline.annotations[0].style["color"] == "red"
    timeline.undo()
    assert timeline.annotations[0].style == {}


def test_restore_intro_instruction_restores_only_missing_opening():
    timeline = Timeline(120)
    timeline.cut(0, 30)
    result = apply_interpretation(interpret("Actually, put the intro back", timeline), timeline)
    assert result["status"] == "applied"
    assert timeline.duration == 120


def test_restore_numbered_history_entry():
    timeline = Timeline(100)
    timeline.cut(0, 10)
    timeline.cut(70, 80)
    response = apply_interpretation(interpret("Restore edit 1", timeline), timeline)
    assert response["status"] == "applied"
    assert response["summary"]["restored_step"] == 1
    assert timeline.original_to_current(75) == 65


def test_timed_caption_on_cut_material_is_rejected():
    timeline = Timeline(30)
    timeline.cut(10, 20)
    result = apply_interpretation(interpret("Add caption 'Gone' at 00:12", timeline), timeline)
    assert result["status"] == "rejected"


def test_unsupported_instruction_is_rejected_instead_of_guessed():
    assert interpret("Make the boring part shorter", make_timeline()).status == "rejected"
