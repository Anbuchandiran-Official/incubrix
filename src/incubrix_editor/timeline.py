"""Deterministic, reversible editing over an immutable source timeline.

All source ranges use seconds from the original media. A timeline is an ordered
list of source-backed segments; this makes cuts and reorders predictable while
word-anchored annotations remain attached to their source words.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any, Literal


class TimelineError(ValueError):
    pass


@dataclass
class Segment:
    source_start: float
    source_end: float
    speed: float = 1.0

    @property
    def duration(self) -> float:
        return (self.source_end - self.source_start) / self.speed


@dataclass
class Word:
    id: str
    text: str
    start: float
    end: float
    speaker: str | None = None
    confidence: float = 1.0


@dataclass
class Annotation:
    id: str
    kind: Literal["caption", "mute", "bleep"]
    start: float
    end: float
    text: str | None = None
    style: dict[str, Any] = field(default_factory=dict)
    word_ids: list[str] = field(default_factory=list)


@dataclass
class Snapshot:
    label: str
    segments: list[Segment]
    annotations: list[Annotation]


class Timeline:
    """A timeline with stable source-time anchors and snapshot-based history."""

    def __init__(self, duration_s: float, words: list[Word] | None = None):
        if duration_s <= 0:
            raise TimelineError("Source duration must be positive")
        self.source_duration = float(duration_s)
        self.words = deepcopy(words or [])
        self.segments = [Segment(0.0, self.source_duration)]
        self.annotations: list[Annotation] = []
        self._undo: list[Snapshot] = []
        self._redo: list[Snapshot] = []
        self._history: list[str] = []
        self._history_snapshots: list[Snapshot] = []

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.segments)

    def _snapshot(self, label: str) -> Snapshot:
        return Snapshot(label, deepcopy(self.segments), deepcopy(self.annotations))

    def _begin_edit(self, label: str) -> None:
        self._undo.append(self._snapshot(label))
        self._redo.clear()
        self._history.append(label)

    def _finish_edit(self) -> None:
        self._history_snapshots.append(self._snapshot(self._history[-1]))

    def _validate_range(self, start: float, end: float) -> None:
        if start < 0 or end <= start or end > self.source_duration:
            raise TimelineError(f"Invalid original-time range {start:g}-{end:g}s")

    def cut(self, start: float, end: float) -> dict[str, Any]:
        """Remove an original-time interval, splitting retained source segments."""
        self._validate_range(start, end)
        self._begin_edit(f"cut {start:g}-{end:g}")
        next_segments: list[Segment] = []
        removed = 0.0
        for seg in self.segments:
            left, right = max(start, seg.source_start), min(end, seg.source_end)
            if left >= right:
                next_segments.append(seg)
                continue
            removed += (right - left) / seg.speed
            if seg.source_start < left:
                next_segments.append(Segment(seg.source_start, left, seg.speed))
            if right < seg.source_end:
                next_segments.append(Segment(right, seg.source_end, seg.speed))
        if removed == 0:
            self._undo.pop()
            self._history.pop()
            raise TimelineError("The requested range is already removed")
        self.segments = next_segments
        self._prune_annotations()
        self._finish_edit()
        return {"removed_s": round(removed, 6), "duration_s": round(self.duration, 6)}

    def cut_ranges(self, ranges: list[tuple[float, float]]) -> dict[str, Any]:
        """Remove multiple original-time ranges as one undoable edit."""
        normalized = sorted((float(start), float(end)) for start, end in ranges)
        if not normalized:
            raise TimelineError("At least one range is required")
        merged: list[list[float]] = []
        for start, end in normalized:
            self._validate_range(start, end)
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        self._begin_edit("remove filler words")
        kept: list[Segment] = []
        removed = 0.0
        for segment in self.segments:
            pieces = [(segment.source_start, segment.source_end)]
            for start, end in merged:
                next_pieces: list[tuple[float, float]] = []
                for left, right in pieces:
                    cut_left, cut_right = max(left, start), min(right, end)
                    if cut_left >= cut_right:
                        next_pieces.append((left, right))
                        continue
                    removed += (cut_right - cut_left) / segment.speed
                    if left < cut_left:
                        next_pieces.append((left, cut_left))
                    if cut_right < right:
                        next_pieces.append((cut_right, right))
                pieces = next_pieces
            kept.extend(Segment(left, right, segment.speed) for left, right in pieces if left < right)
        if removed <= 0:
            self._undo.pop()
            self._history.pop()
            raise TimelineError("The requested filler words are already removed")
        self.segments = kept
        self._prune_annotations()
        self._finish_edit()
        return {"removed_s": round(removed, 6), "duration_s": round(self.duration, 6),
                "ranges": [[start, end] for start, end in merged]}

    def _annotation_survives(self, annotation: Annotation) -> bool:
        return any(s.source_start < annotation.end and s.source_end > annotation.start
                   for s in self.segments)

    def _prune_annotations(self) -> None:
        by_id = {word.id: word for word in self.words}
        kept: list[Annotation] = []
        for annotation in self.annotations:
            if annotation.word_ids:
                visible = [by_id[word_id] for word_id in annotation.word_ids
                           if word_id in by_id and any(s.source_start <= by_id[word_id].start
                                                       and s.source_end >= by_id[word_id].end
                                                       for s in self.segments)]
                if not visible:
                    continue
                annotation.word_ids = [word.id for word in visible]
                annotation.start = min(word.start for word in visible)
                annotation.end = max(word.end for word in visible)
                kept.append(annotation)
            elif self._annotation_survives(annotation):
                kept.append(annotation)
        self.annotations = kept

    def keep(self, start: float, end: float) -> dict[str, Any]:
        self._validate_range(start, end)
        self._begin_edit(f"keep {start:g}-{end:g}")
        kept: list[Segment] = []
        for seg in self.segments:
            left, right = max(start, seg.source_start), min(end, seg.source_end)
            if left < right:
                kept.append(Segment(left, right, seg.speed))
        if not kept:
            self._undo.pop()
            self._history.pop()
            raise TimelineError("The requested range contains no current content")
        self.segments = kept
        self._prune_annotations()
        self._finish_edit()
        return {"duration_s": round(self.duration, 6)}

    def set_speed(self, start: float, end: float, speed: float) -> dict[str, Any]:
        self._validate_range(start, end)
        if not 0.25 <= speed <= 4.0:
            raise TimelineError("Speed must be between 0.25x and 4x")
        self._begin_edit(f"speed {start:g}-{end:g} {speed:g}x")
        result: list[Segment] = []
        for seg in self.segments:
            left, right = max(start, seg.source_start), min(end, seg.source_end)
            if left >= right:
                result.append(seg)
                continue
            if seg.source_start < left:
                result.append(Segment(seg.source_start, left, seg.speed))
            result.append(Segment(left, right, speed))
            if right < seg.source_end:
                result.append(Segment(right, seg.source_end, seg.speed))
        self.segments = result
        self._finish_edit()
        return {"duration_s": round(self.duration, 6)}

    def reorder(self, start: float, end: float, destination_s: float) -> dict[str, Any]:
        """Move a source-time section to a current-timeline insertion point.

        The selected source range must be continuous and fully present. The
        destination is expressed in current timeline time; the moved section is
        inserted before the segment at that position. Existing time effects on
        the moved range are preserved.
        """
        self._validate_range(start, end)
        if not 0 <= destination_s <= self.duration:
            raise TimelineError("Reorder destination is outside the current timeline")
        if any(s.source_start < end and s.source_end > start for s in self.segments) is False:
            raise TimelineError("The selected source range is not present")
        covered = sum(max(0.0, min(end, s.source_end) - max(start, s.source_start)) for s in self.segments)
        if abs(covered - (end - start)) > 1e-7:
            raise TimelineError("The selected range is partially cut and cannot be reordered as one section")
        self._begin_edit(f"reorder {start:g}-{end:g}")
        moving: list[Segment] = []
        remainder: list[Segment] = []
        for seg in self.segments:
            left, right = max(start, seg.source_start), min(end, seg.source_end)
            if left < right:
                if seg.source_start < left: remainder.append(Segment(seg.source_start, left, seg.speed))
                moving.append(Segment(left, right, seg.speed))
                if right < seg.source_end: remainder.append(Segment(right, seg.source_end, seg.speed))
            else:
                remainder.append(seg)
        current = 0.0
        insertion_index = len(remainder)
        for i, seg in enumerate(remainder):
            if current >= destination_s:
                insertion_index = i
                break
            if current < destination_s < current + seg.duration:
                # Split the segment so insertion happens at the requested time.
                source_split = seg.source_start + (destination_s - current) * seg.speed
                remainder[i:i+1] = [Segment(seg.source_start, source_split, seg.speed),
                                     Segment(source_split, seg.source_end, seg.speed)]
                insertion_index = i + 1
                break
            current += seg.duration
        self.segments = remainder[:insertion_index] + moving + remainder[insertion_index:]
        self._finish_edit()
        return {"duration_s": round(self.duration, 6), "moved_source_range": [start, end],
                "inserted_at_current_s": destination_s}

    def add_word_effect(self, kind: Literal["mute", "bleep"], word_ids: list[str]) -> Annotation:
        known = {w.id: w for w in self.words}
        selected = [known[i] for i in word_ids if i in known]
        if not selected:
            raise TimelineError("Effect must target at least one known transcript word")
        selected = [word for word in selected if any(
            segment.source_start <= word.start and segment.source_end >= word.end for segment in self.segments)]
        if not selected:
            raise TimelineError("The selected transcript words are not present in the current timeline")
        self._begin_edit(f"{kind} words")
        annotation = Annotation(f"{kind}-{len(self.annotations)+1}", kind,
                                min(w.start for w in selected), max(w.end for w in selected),
                                word_ids=[w.id for w in selected])
        self.annotations.append(annotation)
        self._finish_edit()
        return annotation

    def add_caption(self, text: str, word_ids: list[str], style: dict[str, Any] | None = None) -> Annotation:
        known = {w.id: w for w in self.words}
        selected = [known[i] for i in word_ids if i in known]
        if not selected:
            raise TimelineError("Caption must anchor to at least one known transcript word")
        selected = [word for word in selected if any(
            segment.source_start <= word.start and segment.source_end >= word.end for segment in self.segments)]
        if not selected:
            raise TimelineError("The selected transcript words are not present in the current timeline")
        annotation = Annotation(f"caption-{len(self.annotations)+1}", "caption",
                                min(w.start for w in selected), max(w.end for w in selected),
                                text=text, style=style or {}, word_ids=[w.id for w in selected])
        self._begin_edit("add caption")
        self.annotations.append(annotation)
        self._finish_edit()
        return annotation

    def add_caption_at(self, text: str, start: float, end: float, style: dict[str, Any] | None = None) -> Annotation:
        """Add a time-anchored caption with no transcript-word dependency."""
        self._validate_range(start, end)
        if not text.strip():
            raise TimelineError("Caption text is required")
        if not any(segment.source_start < end and segment.source_end > start for segment in self.segments):
            raise TimelineError("The requested caption time is cut from the current timeline")
        self._begin_edit("add caption")
        annotation = Annotation(f"caption-{len(self.annotations)+1}", "caption", start, end,
                                text=text, style=style or {}, word_ids=[])
        self.annotations.append(annotation)
        self._finish_edit()
        return annotation

    def update_annotation(self, annotation_id: str, **changes: Any) -> Annotation:
        annotation = next((a for a in self.annotations if a.id == annotation_id), None)
        if annotation is None:
            raise TimelineError(f"Annotation {annotation_id!r} does not exist")
        self._begin_edit(f"update {annotation_id}")
        for key, value in changes.items():
            if key not in {"text", "style"}:
                raise TimelineError(f"Cannot update annotation field {key!r}")
            setattr(annotation, key, value)
        self._finish_edit()
        return annotation

    def undo(self) -> str:
        if not self._undo:
            raise TimelineError("Nothing to undo")
        current = self._snapshot("redo")
        previous = self._undo.pop()
        self._redo.append(current)
        self.segments, self.annotations = deepcopy(previous.segments), deepcopy(previous.annotations)
        self._history.append("undo")
        self._history_snapshots.append(self._snapshot("undo"))
        return previous.label

    def redo(self) -> str:
        if not self._redo:
            raise TimelineError("Nothing to redo")
        current = self._snapshot("undo")
        next_state = self._redo.pop()
        self._undo.append(current)
        self.segments, self.annotations = deepcopy(next_state.segments), deepcopy(next_state.annotations)
        self._history.append("redo")
        self._history_snapshots.append(self._snapshot("redo"))
        return next_state.label

    def restore_source_range(self, start: float, end: float) -> dict[str, Any]:
        """Restore the original source range in its original-time position."""
        self._validate_range(start, end)
        self._begin_edit(f"restore {start:g}-{end:g}")
        boundaries = sorted({start, end,
                             *(max(start, s.source_start) for s in self.segments if s.source_start < end and s.source_end > start),
                             *(min(end, s.source_end) for s in self.segments if s.source_start < end and s.source_end > start)})
        missing: list[tuple[float, float]] = []
        for left, right in zip(boundaries, boundaries[1:]):
            midpoint = (left + right) / 2
            if not any(s.source_start <= midpoint < s.source_end for s in self.segments):
                missing.append((left, right))
        for left, right in missing:
            predecessor = [(i, seg) for i, seg in enumerate(self.segments) if seg.source_end <= left]
            successor = [(i, seg) for i, seg in enumerate(self.segments) if seg.source_start >= right]
            prev = max(predecessor, key=lambda pair: pair[1].source_end) if predecessor else None
            nxt = min(successor, key=lambda pair: pair[1].source_start) if successor else None
            if prev and nxt and prev[0] < nxt[0]:
                insertion = nxt[0]
            elif prev:
                insertion = prev[0] + 1
            elif nxt:
                insertion = nxt[0]
            else:
                insertion = len(self.segments)
            self.segments.insert(insertion, Segment(left, right, 1.0))
        self.segments = self._merge_adjacent(self.segments)
        self._prune_annotations()
        self._finish_edit()
        return {"duration_s": round(self.duration, 6)}

    def restore_edit(self, step: int) -> dict[str, Any]:
        """Restore the timeline state immediately after a numbered history step."""
        if step < 1 or step > len(self._history_snapshots):
            raise TimelineError(f"History step must be between 1 and {len(self._history_snapshots)}")
        self._begin_edit(f"restore edit {step}")
        snapshot = self._history_snapshots[step - 1]
        self.segments, self.annotations = deepcopy(snapshot.segments), deepcopy(snapshot.annotations)
        self._finish_edit()
        return {"restored_step": step, "duration_s": round(self.duration, 6)}

    @staticmethod
    def _merge_adjacent(segments: list[Segment]) -> list[Segment]:
        merged: list[Segment] = []
        for seg in segments:
            if merged and abs(merged[-1].source_end - seg.source_start) < 1e-9 and merged[-1].speed == seg.speed:
                merged[-1].source_end = seg.source_end
            else:
                merged.append(seg)
        return merged

    def original_to_current(self, source_time: float) -> float | None:
        """Map a source timestamp to current presentation time, or None if cut."""
        offset = 0.0
        for seg in self.segments:
            if seg.source_start <= source_time < seg.source_end:
                return offset + (source_time - seg.source_start) / seg.speed
            offset += seg.duration
        return None

    def as_dict(self) -> dict[str, Any]:
        body = {"source_duration_s": self.source_duration,
                "duration_s": round(self.duration, 6),
                "time_policy": "original source time for edit ranges; reorder destinations use current timeline time",
                "segments": [asdict(s) for s in self.segments],
                "words": [asdict(w) for w in self.words],
                "annotations": [asdict(a) for a in self.annotations],
                "history": [{"step": i + 1, "label": label} for i, label in enumerate(self._history)]}
        body["timeline_hash"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        return body

