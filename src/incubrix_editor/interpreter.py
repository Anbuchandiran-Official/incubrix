"""Conservative, deterministic instruction-to-edit translation.

This module intentionally implements a narrow grammar. It asks when transcript
matches are ambiguous and rejects unsupported requests instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .timeline import Timeline, TimelineError, Word

SOURCE_TIME_POLICY = "original source time for edit ranges; reorder destinations use current timeline time"


@dataclass
class Interpretation:
    status: str
    operation: dict[str, Any] | None = None
    question: str | None = None
    options: list[str] | None = None
    message: str | None = None
    candidates: list[dict[str, Any]] | None = None


def parse_time(value: str) -> float:
    value = value.strip().lower()
    if ":" in value:
        parts = [int(part) for part in value.split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        raise ValueError("Use seconds, mm:ss, or hh:mm:ss")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds)?", value)
    if match:
        return float(match.group(1))
    raise ValueError(f"Could not read time {value!r}")


def _normal(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.casefold())


def _phrase_matches(words: list[Word], phrase: str) -> list[list[Word]]:
    wanted = _normal(phrase)
    transcript = [_normal(word.text) for word in words]
    flat = [(token, idx) for idx, group in enumerate(transcript) for token in group]
    found: list[list[Word]] = []
    for pos in range(0, len(flat) - len(wanted) + 1):
        if [token for token, _ in flat[pos:pos + len(wanted)]] == wanted:
            ids = list(dict.fromkeys(idx for _, idx in flat[pos:pos + len(wanted)]))
            found.append([words[idx] for idx in ids])
    return found


def _choose_match(matches: list[list[Word]], phrase: str, build_operation: Any) -> Interpretation | list[Word]:
    if not matches:
        return Interpretation("needs_clarification", question=f"I couldn't find '{phrase}' in the transcript. Which wording should I use?", options=[])
    if len(matches) > 1:
        choices = [f"{m[0].start:.2f}s: {' '.join(w.text for w in m)}" for m in matches]
        candidates = [build_operation(match) for match in matches]
        return Interpretation("needs_clarification", question=f"I found {len(matches)} matches for '{phrase}'. Which one should I use?", options=choices, candidates=candidates)
    return matches[0]


def resolve_clarification(question: Interpretation, answer: str) -> Interpretation:
    """Resolve an ordinal answer against the options previously presented."""
    candidates = question.candidates or []
    value = answer.casefold().strip()
    ordinals = {"first": 0, "1st": 0, "one": 0, "second": 1, "2nd": 1, "two": 1,
                "third": 2, "3rd": 2, "three": 2, "fourth": 3, "4th": 3,
                "last": len(candidates) - 1}
    match = re.search(r"\b(first|1st|one|second|2nd|two|third|3rd|three|fourth|4th|last|\d+)\b", value)
    if not match:
        return Interpretation("needs_clarification", question=question.question, options=question.options, candidates=candidates,
                              message="Please choose one of the numbered matches.")
    token = match.group(1)
    index = ordinals.get(token, int(token) - 1 if token.isdigit() else -1)
    if index < 0 or index >= len(candidates):
        return Interpretation("needs_clarification", question=question.question, options=question.options, candidates=candidates,
                              message=f"That choice is outside the {len(candidates)} available matches.")
    return Interpretation("applied", candidates[index])


def interpret(text: str, timeline: Timeline) -> Interpretation:
    raw = text.strip().replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    lower = raw.casefold()
    words = timeline.words

    if re.fullmatch(r"undo", lower):
        return Interpretation("applied", {"operation": "undo"})
    if re.fullmatch(r"redo", lower):
        return Interpretation("applied", {"operation": "redo"})
    m = re.fullmatch(r"restore\s+(?:the\s+)?(?:earlier\s+)?edit\s+#?(\d+)[.!]?", lower)
    if m:
        return Interpretation("applied", {"operation": "restore_edit", "step": int(m.group(1))})

    if re.fullmatch(r"(?:actually,?\s*)?(?:put|restore)\s+(?:the\s+)?intro\s+back[.!]?", lower):
        if timeline.segments and timeline.segments[0].source_start > 0:
            return Interpretation("applied", {"operation": "restore", "start_s": 0.0,
                                                "end_s": timeline.segments[0].source_start})
        return Interpretation("rejected", message="The opening section is already present.")

    m = re.fullmatch(r"make\s+(?:that\s+)?caption\s+(?:text\s+)?(?:color\s+)?(red|blue|green|white|yellow|black)[.!]?", lower)
    if m:
        captions = [a for a in timeline.annotations if a.kind == "caption"]
        if not captions:
            return Interpretation("rejected", message="There is no caption to update.")
        if len(captions) > 1:
            return Interpretation("needs_clarification", question="Which caption should I recolor?",
                                  options=[f"{a.text} at {a.start:.2f}s" for a in captions],
                                  candidates=[{"operation": "update_caption", "annotation_id": a.id,
                                               "style": {**a.style, "color": m.group(1)}} for a in captions])
        return Interpretation("applied", {"operation": "update_caption", "annotation_id": captions[0].id,
                                            "style": {**captions[0].style, "color": m.group(1)}})

    m = re.fullmatch(r"(?:cut|remove)\s+(?:the\s+)?first\s+(\d+(?:\.\d+)?)\s*(?:s|sec|seconds?)", lower)
    if m:
        return Interpretation("applied", {"operation": "cut", "start_s": 0.0, "end_s": float(m.group(1))})

    m = re.fullmatch(r"(?:cut|remove)\s+(?:the\s+)?last\s+(\d+(?:\.\d+)?)\s*(?:s|sec|seconds?)[.!]?", lower)
    if m:
        length = float(m.group(1))
        if length >= timeline.source_duration:
            return Interpretation("rejected", message="The last-section duration must be shorter than the source video.")
        return Interpretation("applied", {"operation": "cut",
                                            "start_s": timeline.source_duration - length,
                                            "end_s": timeline.source_duration})

    m = re.fullmatch(r"(?:cut|remove)\s+(?:from\s+)?([\d:.]+)\s*(?:to|-)\s*([\d:.]+)", lower)
    if m:
        try:
            start, end = parse_time(m.group(1)), parse_time(m.group(2))
            return Interpretation("applied", {"operation": "cut", "start_s": start, "end_s": end})
        except ValueError as exc:
            return Interpretation("rejected", message=str(exc))

    m = re.fullmatch(r"keep\s+(?:from\s+)?([\d:.]+)\s+to\s+([\d:.]+)[.!]?", lower)
    if m:
        try:
            return Interpretation("applied", {"operation": "keep", "start_s": parse_time(m.group(1)),
                                                "end_s": parse_time(m.group(2))})
        except ValueError as exc:
            return Interpretation("rejected", message=str(exc))

    m = re.fullmatch(r"(?:move|reorder)\s+(?:the\s+)?(?:part\s+)?from\s+([\d:.]+)\s+to\s+([\d:.]+)\s+(?:to|at)\s+([\d:.]+)[.!]?", lower)
    if m:
        try:
            return Interpretation("applied", {"operation": "reorder", "start_s": parse_time(m.group(1)),
                                                "end_s": parse_time(m.group(2)),
                                                "destination_s": parse_time(m.group(3))})
        except ValueError as exc:
            return Interpretation("rejected", message=str(exc))

    m = re.fullmatch(r"(speed\s+up|speed\s+up\s+the\s+video|slow\s+down|slow\s+down\s+the\s+video)\s+from\s+([\d:.]+)\s+to\s+([\d:.]+)\s+by\s+(\d+(?:\.\d+)?)\s*x[.!]?", lower)
    if m:
        try:
            start, end = parse_time(m.group(2)), parse_time(m.group(3))
        except ValueError as exc:
            return Interpretation("rejected", message=str(exc))
        amount = float(m.group(4))
        speed = 1 / amount if m.group(1).startswith("slow") else amount
        return Interpretation("applied", {"operation": "speed", "start_s": start, "end_s": end,
                                            "speed": speed})

    m = re.fullmatch(r"(?:cut|remove)\s+(?:the\s+)?part\s+about\s+(.+?)[.!?]?$", raw, flags=re.I)
    if m:
        phrase = m.group(1).strip(" '\"“”")
        selected = _choose_match(_phrase_matches(words, phrase), phrase,
                                 lambda match: {"operation": "cut", "start_s": match[0].start,
                                                "end_s": match[-1].end, "matched_word_ids": [w.id for w in match]})
        if isinstance(selected, Interpretation): return selected
        return Interpretation("applied", {"operation": "cut", "start_s": selected[0].start, "end_s": selected[-1].end,
                                            "matched_word_ids": [w.id for w in selected]})

    m = re.fullmatch(r"add\s+a\s+caption\s+(['\"])(.*?)\1\s+when\s+i\s+say\s+(.+?)[.!?]?$", raw, flags=re.I)
    if m:
        caption, phrase = m.group(2), m.group(3).strip(" '\"“”")
        selected = _choose_match(_phrase_matches(words, phrase), phrase,
                                 lambda match: {"operation": "caption", "text": caption,
                                                "word_ids": [w.id for w in match]})
        if isinstance(selected, Interpretation): return selected
        return Interpretation("applied", {"operation": "caption", "text": caption,
                                            "word_ids": [w.id for w in selected]})

    time_value = r"(?:\d{1,2}:\d{2}(?::\d{2})?|\d+(?:\.\d+)?)"
    time_pattern = rf"({time_value})"
    # Explicit duration takes precedence over a single timestamp. Accept common
    # phrasing: "from 00:03 to 00:07", "at 00:03 to 00:07", and "between ... and ...".
    m = re.fullmatch(
        rf"add\s+(?:a\s+)?caption\s+(['\"])(.*?)\1\s+(?:from\s+{time_pattern}\s+to\s+{time_pattern}|at\s+{time_pattern}\s+to\s+{time_pattern}|between\s+{time_pattern}\s+and\s+{time_pattern})[.!?]?",
        raw, flags=re.I)
    if m:
        try:
            # The three alternatives each provide a start/end pair in adjacent groups.
            values = [m.group(i) for i in range(3, 9) if m.group(i) is not None]
            start, end = parse_time(values[0]), parse_time(values[1])
        except ValueError as exc:
            return Interpretation("rejected", message=str(exc))
        if start >= end:
            return Interpretation("rejected", message="Caption end time must be after its start time.")
        if start < 0 or end > timeline.source_duration:
            return Interpretation("rejected", message="Caption range must be within the source video.")
        return Interpretation("applied", {"operation": "caption_at", "text": m.group(2),
                                            "start_s": start, "end_s": end})

    m = re.fullmatch(rf"add\s+(?:a\s+)?caption\s+(['\"])(.*?)\1\s+at\s+{time_pattern}[.!?]?", raw, flags=re.I)
    if m:
        try:
            at = parse_time(m.group(3))
        except ValueError as exc:
            return Interpretation("rejected", message=str(exc))
        if at >= timeline.source_duration:
            return Interpretation("rejected", message="Caption time must be before the end of the source.")
        return Interpretation("applied", {"operation": "caption_at", "text": m.group(2), "start_s": at,
                                            "end_s": min(at + 3.0, timeline.source_duration)})

    m = re.fullmatch(r"(mute|bleep)\s+(?:the\s+)?(?:word|phrase)?\s*['\"]?(.+?)['\"]?[.!?]?$", raw, flags=re.I)
    if m:
        kind, phrase = m.group(1).lower(), m.group(2).strip(" '\"“”")
        selected = _choose_match(_phrase_matches(words, phrase), phrase,
                                 lambda match: {"operation": kind, "word_ids": [w.id for w in match]})
        if isinstance(selected, Interpretation): return selected
        return Interpretation("applied", {"operation": kind, "word_ids": [w.id for w in selected]})

    if "filler" in lower and ("remove" in lower or "cut" in lower):
        selected = [w for w in words if _normal(w.text) in (["um"], ["uh"])]
        if not selected:
            return Interpretation("applied", {"operation": "noop", "reason": "No um/uh fillers found"})
        return Interpretation("applied", {"operation": "cut_ranges", "ranges": [[w.start, w.end] for w in selected]})

    return Interpretation("rejected", message="I can't safely map that instruction yet. Try a specific time range, an exact transcript phrase, undo, or redo.")


def apply_interpretation(result: Interpretation, timeline: Timeline) -> dict[str, Any]:
    if result.status != "applied" or not result.operation:
        return {"status": result.status, "question": result.question, "options": result.options,
                "message": result.message, "time_policy": "original source time for all instructions"}
    op = dict(result.operation)
    kind = op.pop("operation")
    time_policy = SOURCE_TIME_POLICY if kind == "reorder" else "original source time for all instructions"
    try:
        if kind == "cut": summary = timeline.cut(op["start_s"], op["end_s"])
        elif kind == "keep": summary = timeline.keep(op["start_s"], op["end_s"])
        elif kind == "reorder": summary = timeline.reorder(op["start_s"], op["end_s"], op["destination_s"])
        elif kind == "speed": summary = timeline.set_speed(op["start_s"], op["end_s"], op["speed"])
        elif kind == "restore": summary = timeline.restore_source_range(op["start_s"], op["end_s"])
        elif kind == "restore_edit": summary = timeline.restore_edit(op["step"])
        elif kind == "update_caption":
            annotation = timeline.update_annotation(op["annotation_id"], style=op["style"])
            summary = {"annotation_id": annotation.id, "style": annotation.style}
        elif kind == "caption":
            annotation = timeline.add_caption(op["text"], op["word_ids"])
            summary = {"annotation_id": annotation.id}
        elif kind == "caption_at":
            annotation = timeline.add_caption_at(op["text"], op["start_s"], op["end_s"])
            summary = {"annotation_id": annotation.id, "start_s": annotation.start, "end_s": annotation.end}
        elif kind in {"mute", "bleep"}:
            annotation = timeline.add_word_effect(kind, op["word_ids"])
            summary = {"annotation_id": annotation.id}
        elif kind == "cut_ranges":
            summary = timeline.cut_ranges([tuple(item) for item in op["ranges"]])
        elif kind == "undo": summary = {"reversed": timeline.undo()}
        elif kind == "redo": summary = {"restored": timeline.redo()}
        elif kind == "noop": summary = {"reason": op["reason"]}
        else: raise TimelineError("Unsupported operation")
        return {"status": "applied", "operations": [result.operation], "summary": summary,
                "time_policy": time_policy}
    except (TimelineError, KeyError) as exc:
        return {"status": "rejected", "message": str(exc),
                "time_policy": time_policy}

