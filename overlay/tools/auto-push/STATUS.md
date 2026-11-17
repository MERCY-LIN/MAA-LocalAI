# MAA LocalAI Auto-Push Status

Updated: 2026-11-16

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
- Shared `StageExecutionConfig` validation for single-stage and campaign runs,
  including `verify_threshold` and the same formal-battle safety defaults.
- Settlement capture is performed synchronously by a MaaCore task plugin while
  the matched result frame is still owned by the task thread. Python no longer
  captures from the asynchronous callback thread, avoiding the thread-affine
  device mutex conflict.
- Conservative screenshot/Core evidence reconciliation. Conflicting evidence
  is preserved as `conflict`, never promoted to a three-star success.
- Unique full-roster lineup assignment with separate `requirements_satisfied`
  and attemptable-low-investment states. Campaign selection only starts jobs
  whose declared elite, level, skill, module, substitution, and uniqueness
  constraints are satisfied.
- Unknown settlement results pause the trial scheduler until explicit recovery
  evidence is recorded. Exact stage/job binding is checked before preflight.
- Structured step evidence, external real-screenshot corpus checks, and replay
  of saved recognition, callback state transitions, and evidence decisions.
- Persistent multi-stage campaign queue with candidate switching after explicit
  non-three-star results, per-stage and total attempt limits, elapsed-time and
  sanity budgets, and atomic checkpoint recovery. The attempt and sanity cost
  are persisted before formal execution and reconciled before resume.
- Cross-stage campaigns return from the previous stage detail page, navigate
  through MaaCore to the next stage, and require an exact target-detail preflight.
- Unknown, conflicting, blocked, or budget-exhausted campaign states pause the
  queue and require recovery evidence instead of silently starting another run.
- Post-stop safe-exit verification now captures and recognizes the page after
  Core ends the session; only known safe states set `safe_exit_verified=true`.
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
- A human-reviewed real-screenshot corpus has not been supplied or run in this
  checkout, so recognition accuracy against game-version changes remains
  `UNVERIFIED`.
- Live scheduling replay, automatic post-conflict recovery, GUI integration with
  the campaign queue, and active three-star impossibility detection remain
  incomplete. Safe exit is verified for observed post-stop pages but has not
  been proven in a real interrupted battle.

## Safety boundaries

- Normal repeated input uses ADB or MAA, not model vision or per-frame analysis.
- Computer Use is recorded but does not cap the number of operations.
- Local automation must remain usable without Codex or an online model.
- A task completion callback is never treated as a three-star clear.
- Exercise is never a prerequisite for a formal run.
- Sanity potions and Originite Prime remain disabled unless explicitly enabled
  by the user.
