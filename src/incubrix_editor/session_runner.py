"""Run a JSON/YAML-like session file through the local timeline engine.

The first runner version accepts JSON. Steps are structured edit objects so
timeline correctness is testable independently of natural-language parsing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .interpreter import apply_interpretation, interpret, resolve_clarification
from .timeline import Timeline, TimelineError, Word


def run_session(payload: dict[str, Any]) -> dict[str, Any]:
    timeline = Timeline(payload["source_duration_s"], [Word(**w) for w in payload.get("words", [])])
    output = []
    pending = None
    for index, step in enumerate(payload.get("steps", []), start=1):
        try:
            if "say" in step:
                interpretation = interpret(step["say"], timeline)
                pending = interpretation if interpretation.status == "needs_clarification" else None
                if pending and step.get("answer"):
                    interpretation = resolve_clarification(pending, step["answer"])
                    pending = interpretation if interpretation.status == "needs_clarification" else None
                response = apply_interpretation(interpretation, timeline)
                result = response
                status, message = response["status"], response.get("message")
                if status == "needs_clarification" and step.get("answer"):
                    status, message = "needs_clarification", response.get("message")
            elif "answer" in step:
                if pending is None:
                    raise TimelineError("There is no pending clarification to answer")
                interpretation = resolve_clarification(pending, step["answer"])
                response = apply_interpretation(interpretation, timeline)
                result = response
                status, message = response["status"], response.get("message")
                pending = interpretation if status == "needs_clarification" else None
            else:
                operation = step["operation"]
                if operation == "cut": result = timeline.cut(step["start_s"], step["end_s"])
                elif operation == "keep": result = timeline.keep(step["start_s"], step["end_s"])
                elif operation == "speed": result = timeline.set_speed(step["start_s"], step["end_s"], step["speed"])
                elif operation == "reorder": result = timeline.reorder(step["start_s"], step["end_s"], step["destination_s"])
                elif operation == "restore": result = timeline.restore_source_range(step["start_s"], step["end_s"])
                elif operation == "restore_edit": result = timeline.restore_edit(step["step"])
                elif operation == "caption":
                    a = timeline.add_caption(step["text"], step["word_ids"], step.get("style"))
                    result = {"annotation_id": a.id}
                elif operation in {"mute", "bleep"}:
                    a = timeline.add_word_effect(operation, step["word_ids"])
                    result = {"annotation_id": a.id}
                elif operation == "undo": result = {"reversed": timeline.undo()}
                elif operation == "redo": result = {"restored": timeline.redo()}
                else: raise TimelineError(f"Unsupported structured operation: {operation}")
                status, message = "applied", None
        except (TimelineError, KeyError) as exc:
            status, result, message = "rejected", {}, str(exc)
        output.append({"step": index, "status": status, "operations": [step] if status == "applied" else [],
                       "summary": result, "message": message, "timeline_hash": timeline.as_dict()["timeline_hash"],
                       "duration_s": round(timeline.duration, 6),
                       "time_policy": ("original source time for edit ranges; reorder destinations use current timeline time"
                                       if operation == "reorder" else "original source time for all instructions")})
    return {"results": output, "final_timeline": timeline.as_dict()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a structured IncuBrix editing session")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, default=Path("results.json"))
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    result = run_session(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()

