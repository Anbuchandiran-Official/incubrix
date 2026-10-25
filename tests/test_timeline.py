from incubrix_editor.timeline import Timeline, TimelineError, Word


def words():
    return [Word("w1", "Welcome", 42.0, 42.4), Word("w2", "to", 42.4, 42.6),
            Word("w3", "channel", 42.6, 42.9), Word("w4", "pricing", 80.0, 80.6)]


def test_cut_maps_original_times_and_undo_redo():
    tl = Timeline(120, words())
    tl.add_caption("Welcome!", ["w1", "w2", "w3"])
    tl.cut(0, 30)
    assert tl.duration == 90
    assert tl.original_to_current(42.0) == 12.0
    assert tl.annotations[0].start == 42.0
    tl.undo()
    assert tl.duration == 120
    tl.redo()
    assert tl.duration == 90


def test_fully_cut_caption_word_is_removed_and_partial_caption_reanchors():
    tl = Timeline(120, words())
    tl.add_caption("Welcome!", ["w1", "w2", "w3"])
    tl.cut(42.0, 42.5)
    assert tl.annotations[0].word_ids == ["w3"]
    assert tl.annotations[0].start == 42.6


def test_speed_changes_current_time_not_source_anchor():
    tl = Timeline(120, words())
    tl.cut(0, 30)
    tl.set_speed(40, 60, 2)
    assert tl.original_to_current(50) == 15
    assert tl.duration == 80


def test_restore_range_preserves_other_edits():
    tl = Timeline(120)
    tl.cut(0, 30)
    tl.cut(70, 80)
    tl.set_speed(40, 60, 2)
    tl.restore_source_range(0, 30)
    assert tl.original_to_current(10) == 10
    assert tl.original_to_current(75) is None
    assert tl.original_to_current(50) == 45
    assert tl.duration == 100


def test_reorder_preserves_duration_and_maps_moved_section():
    tl = Timeline(100)
    tl.reorder(40, 60, 0)
    assert tl.duration == 100
    assert tl.original_to_current(50) == 10
    assert tl.original_to_current(10) == 30


def test_invalid_and_repeat_cut_rejected_without_extra_history():
    tl = Timeline(20)
    tl.cut(0, 5)
    with pytest.raises(TimelineError):
        tl.cut(0, 5)
    assert tl.duration == 15
    tl.undo()
    assert tl.duration == 20


def test_restore_numbered_history_state_is_one_undoable_edit():
    tl = Timeline(100)
    tl.cut(0, 10)
    tl.set_speed(20, 40, 2)
    tl.cut(70, 80)
    restored = tl.restore_edit(1)
    assert restored["restored_step"] == 1
    assert tl.original_to_current(5) is None
    assert tl.original_to_current(75) == 65
    tl.undo()
    assert tl.original_to_current(75) is None


import pytest
