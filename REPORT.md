# Assessment 2 engineering status

This repository is an early, local prototype. The deterministic timeline engine and scripted session runner cover part of the assessment. This report records gaps plainly; the project is not ready to claim that all must-haves pass.

## Implemented and locally checked

- Edits operate on source-time segments; changing the timeline does not write to a source video file.
- Caption, mute, and bleep annotations may target transcript word IDs. Captions remain associated with those words when cuts or speed changes alter current-time offsets.
- Supported natural-language requests return `applied`, `needs_clarification`, or `rejected`. Ambiguous transcript phrases return numbered candidates.
- Filler-word removal cuts detected `um` and `uh` ranges as one compound, undoable timeline edit.
- Timeline mutation is ordinary Python code. The interpreter is a deterministic ruleset, not an AI model.
- The time policy is original source time and appears in instruction results. Speed changes use the same policy.
- Undo/redo operate on snapshots. Each accepted edit gets a history entry; a new edit clears the redo stack.
- Numbered history entries can be restored explicitly; the restore itself is a history step.
- The JSON session runner is covered by unit tests. Repeatable timeline states are represented by a SHA-256 `timeline_hash`.
- Uploaded originals are stored under their SHA-256 and edits render to a separate output file. The UI exposes render and download controls.
- The FFmpeg renderer handles cuts, reordering, speed, captions, mute, and bleep. A local smoke run checked output duration and audio/video end alignment within one source frame.
- Image generation, related endpoints and assets, API-key settings, and generation tests have been removed.

## Assessment requirements still not met

- The preview shows the untouched source; users must render to inspect the edited result.
- The natural-language grammar is intentionally narrow; several edit forms from the assessment are not yet supported. Video transcription is not implemented; transcript words must be supplied by the user.
- Sessions and history exist only in process memory. Restart persistence and review-period availability are not implemented.
- The local automated renderer integration test is skipped when `ffprobe` is unavailable. Docker installs both FFmpeg and FFprobe; broader scripted media fixtures and integration-standard/live-demo validation remain to be completed.

## Verification

Earlier local verification recorded 29 passing tests and one renderer integration test skipped because `ffprobe` was unavailable. That run predates compound filler removal and does not verify the current change. A separate local FFmpeg smoke run exercised cuts, speed, reorder, caption, mute, and bleep and checked duration and stream alignment. These checks do not establish deployment readiness or full assessment compliance.

## Next work required before submission

Expand instruction coverage and scripted media fixtures, add durable session/history storage, run the renderer integration suite with FFmpeg and FFprobe, and validate the integration standard and live demo before submitting evidence. Deployment also requires a Git remote connected in Render; this checkout currently has no commits or remote, so no hosted URL has been created.
