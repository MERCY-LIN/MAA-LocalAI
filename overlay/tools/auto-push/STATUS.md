# MAA LocalAI Auto-Push Status

Updated: 2026-11-12

## Current milestone

The local workflow now covers roster recognition, exact-stage Copilot job
download, compatibility ranking, bounded ADB operations, MAA execution,
OpenCV result verification, and auditable per-round metrics.

## Implemented

- OpenAI-compatible local AI planner with loopback-only defaults and strict
  stage-plan validation.
- MAA `OperBox` recognition through the local C ABI adapter.
- Exact-stage PRTS Plus job queries with full action download.
- True-roster strategy compatibility ranking.
- ADB screenshot throttling, input helpers, visual state memory, and navigation
  preconditions.
- Trial scheduler that defaults to a formal run; drills are optional only.
- JSON debug CLI with run/attempt IDs, atomic status, append-only callbacks,
  preflight screenshots, bounded stop requests, and saved-result replay.
- Numeric MaaCore callback protocol with task-id correlation, explicit terminal
  phases, screenshot sequence/timestamps, and a Python/WPF device mutex.
- Per-round records for Computer Use operations, screenshots, tool calls,
  DeepSeek input/output tokens, strategy runs, and test results.
- Python tests, LocalAI C# contract tests, WPF compile verification, VS Code
  tasks, and a Windows GitHub Actions validation workflow.

## TL-9 / EX progress

- Account roster recognized successfully: 328 owned operators.
- TL-9 and TL-EX-1 through TL-EX-8 job catalogs downloaded and ranked.
- TL-9 high-heat job `108702` matches owned operators, but its first runs failed
  result verification.
- The packaged `MaaCore.dll` predates the local exercise-mode patch. Exercise is
  not a prerequisite for formal runs, and no successful stage clear is claimed.
- The next milestone is to validate the JSON debugging CLI against one real
  formal stage, then add active three-star impossibility detection and verified
  safe battle exit.

## Safety boundaries

- Normal repeated input uses ADB or MAA, not model vision or per-frame analysis.
- Computer Use is recorded but does not cap the number of operations.
- Local automation must remain usable without Codex or an online model.
- A task completion callback is never treated as a three-star clear.
- Exercise is never a prerequisite for a formal run.
- Sanity potions and Originite Prime remain disabled unless explicitly enabled
  by the user.
