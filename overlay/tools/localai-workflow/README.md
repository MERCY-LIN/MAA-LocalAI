# LocalAI development workflow

`localai.ps1` is the single entry point for the local AI planner, Auto-Push tools,
the WPF build, and the low-resource runtime package.

Paths can be overridden with environment variables:

- `MAA_LOCALAI_PACKAGE_DIR`
- `MAA_LOCALAI_DOTNET`
- `MAA_LOCALAI_PYTHON`

## Daily commands

```powershell
cd D:\work\MaaAssistantArknights

# Check toolchain, package, Ollama model, ADB, and running processes.
powershell -NoProfile -ExecutionPolicy Bypass -File tools\localai-workflow\localai.ps1 doctor

# Run Python and C# tests plus a WPF compile check.
powershell -NoProfile -ExecutionPolicy Bypass -File tools\localai-workflow\localai.ps1 verify

# Faster focused checks.
powershell -NoProfile -ExecutionPolicy Bypass -File tools\localai-workflow\localai.ps1 test-python
powershell -NoProfile -ExecutionPolicy Bypass -File tools\localai-workflow\localai.ps1 test-dotnet

# Build the real Debug output after closing MAA.
powershell -NoProfile -ExecutionPolicy Bypass -File tools\localai-workflow\localai.ps1 build-gui

# Start only the UI and skip core initialization.
powershell -NoProfile -ExecutionPolicy Bypass -File tools\localai-workflow\localai.ps1 run-ui
```

## JSON debugging CLI

Use the `debug` command when Codex or a script needs stable, machine-readable
runtime evidence. Every response includes `schema_version`, `command`, `status`,
`run_id`, `attempt_id`, `timestamp`, structured `data`, `errors`, and artifact
paths.

```powershell
# Check package, MaaCore, ADB, model endpoint, and conflicting processes.
powershell -NoProfile -ExecutionPolicy Bypass -File tools\localai-workflow\localai.ps1 debug doctor

# Capture a screenshot, current task state, Copilot formation, callbacks,
# and binary/resource versions into one run directory.
powershell -NoProfile -ExecutionPolicy Bypass -File tools\localai-workflow\localai.ps1 debug snapshot --stage act55side_08

# Execute one formal clear. The explicit confirmation prevents accidental use.
powershell -NoProfile -ExecutionPolicy Bypass -File tools\localai-workflow\localai.ps1 debug run-stage `
  --stage act55side_08 `
  --copilot "C:\path\to\job.json" `
  --confirm-formal-battle

# Read persistent status or incremental callbacks.
powershell -NoProfile -ExecutionPolicy Bypass -File tools\localai-workflow\localai.ps1 debug status
powershell -NoProfile -ExecutionPolicy Bypass -File tools\localai-workflow\localai.ps1 debug events --after 0

# Request a cooperative MaaCore stop. The runner polls this file.
powershell -NoProfile -ExecutionPolicy Bypass -File tools\localai-workflow\localai.ps1 debug stop

# Re-run saved result recognition and callback summarization.
powershell -NoProfile -ExecutionPolicy Bypass -File tools\localai-workflow\localai.ps1 debug replay
```

Evidence is stored under
`D:\work\CodexOutputs\MAA-LocalAI\data\autopush\evidence\<run_id>\`.
`events.jsonl` is append-only and `status.json` is atomically replaced.

`run-stage` always requests a formal battle and sets sanity-potion use to
`false`; it does not require or schedule a drill. A Core completion callback is
not a clear. Only a locally verified three-star settlement can produce
`SUCCEEDED` with a three-star verification payload. It also requires the preflight screenshot to match one of
the known safe pages in `data/autopush/state-memory`; an unknown game state is
reported as `BLOCKED_PREFLIGHT_STATE` without starting the battle.

Task-chain callbacks are parsed by the numeric MaaCore message IDs and correlated
by `taskid`. `TaskChainStopped`, disconnects, task errors, unknown results, and
budget exhaustion have distinct terminal phases and never become success.

`stop` requests `AsstStop` through the active runner and preserves a pre-stop
screenshot. It intentionally reports `STOPPING_UNVERIFIED` or
`STOPPED_UNVERIFIED`: requesting a
stop does not prove that the battle was safely exited. Active three-star
impossibility detection and verified safe-exit handling remain `UNVERIFIED`.

Python workers and the WPF GUI share a named device mutex derived from the ADB
address. A second controller is rejected before it can send input.

`replay` currently replays settlement recognition and callback summarization.
Full scheduling replay is not implemented yet.

## Validation matrix

| Change | Minimum validation |
| --- | --- |
| `tools/auto-push/**` | `test-python` |
| Debug CLI or evidence schema | `test-python`, one `debug doctor`, and one saved-evidence replay |
| Local AI C# service, view model, XAML | `test-dotnet` and WPF build |
| `resource/tasks/tasks.json` | C# tests, Debug launch, task load check |
| `src/MaaCore/**` | Debug CMake build and the affected runtime flow |
| Packaging or launcher scripts | Compare source and package, then run a package smoke test |

`verify` intentionally writes to `build\workflow\verify` and disables the WPF
post-build resource link. This lets it run while another MAA package is open.

## Safety boundaries

- The model plan is untrusted input. Stage codes must match MAA's current catalog,
  contain no more than five stages, and use 1-99 clears.
- Loopback-only endpoints remain the default. Remote endpoints require an explicit
  user setting and must not be enabled in committed default configuration.
- Tests and workflow commands do not start, stop, or update MAA, MuMu, Ollama,
  or the game.
- User configuration, screenshots, reports, caches, and model files stay outside
  the repository and are never copied into test fixtures.
- The packaged application must not use MAA's built-in updater. Rebase the local
  extension onto a newer upstream version and rebuild instead.
