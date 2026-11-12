---
name: maa-localai-workflow
description: Use when developing, testing, debugging, packaging, or reviewing the MAA LocalAI combat planner, restricted Ollama integration, Auto-Push Python tools, WPF AI page, low-resource launch scripts, or the boundary between the source checkout and D:\work\CodexOutputs\MAA-LocalAI.
metadata:
  short-description: Develop and verify MAA LocalAI safely
---

# MAA LocalAI workflow

Use this skill for the LocalAI and Auto-Push extension. Preserve upstream MAA
behavior outside the extension boundary.

## First actions

1. Read the root `AGENTS.md`.
2. Inspect `git status --short --branch` with a safe-directory override if needed.
3. Identify whether the request targets source (`D:\work\MaaAssistantArknights`)
   or the packaged runtime (`D:\work\CodexOutputs\MAA-LocalAI`).
4. Preserve all existing dirty changes, especially `tools/auto-push/**` and
   `src/MaaWpfGui/**LocalAi*`.

Do not assume that copying source files into the package is a complete build.
The local build also depends on the local .NET SDK, MaaCore output, resources,
and runtime files.

## Code map

- `src/MaaWpfGui/Configuration/Global/LocalAi.cs`: persisted local-model settings.
- `src/MaaWpfGui/Services/LocalAi/LocalAiService.cs`: endpoint validation,
  OpenAI-compatible request, JSON extraction, stage whitelist validation.
- `src/MaaWpfGui/ViewModels/UI/LocalAiViewModel.cs`: planner UI state and applying
  a validated plan to the selected Fight task.
- `src/MaaWpfGui/Views/UI/LocalAiView.xaml`: AI planning page.
- `src/MaaWpfGui/ViewModels/UserControl/TaskQueue/FightTaskUserControlModel.cs`:
  stage-plan application boundary.
- `tools/auto-push/autopush/`: offline planning, ADB, recognition, verification.
- `tools/auto-push/autopush/debug_cli.py`: JSON-first runtime debugging entry.
- `tools/auto-push/autopush/evidence.py`: run IDs, status, events, stop request.
- `tools/localai-workflow/localai.ps1`: project workflow entry point.
- `src/MaaWpfGui.LocalAi.Tests/`: C# contract and safety tests.

## Workflow

Run from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\localai-workflow\localai.ps1 doctor
powershell -NoProfile -ExecutionPolicy Bypass -File tools\localai-workflow\localai.ps1 verify
```

Focused commands are `test-python`, `test-dotnet`, `build-gui`, and `run-ui`.
Read `tools/localai-workflow/README.md` for the full matrix.

Use `localai.ps1 debug <command>` for machine-readable runtime diagnosis. Keep
`run-stage` formal by default and require its explicit confirmation flag. A stop
request is not proof of a safe battle exit, and a Core completion callback is
not a three-star clear.

## Hard boundaries

- Keep the default endpoint loopback-only. Never commit a remote endpoint or
  credential as enabled state.
- Treat model output as untrusted. Validate exact stage codes against the current
  catalog, reject unknown codes, deduplicate, limit to five stages, and clamp
  repetitions to 1-99.
- Do not place screenshots, account data, logs, model responses, tokens, model
  files, or runtime reports in tests or source control.
- Do not auto-start, stop, update, or lower priority for MAA, MuMu, Ollama, or
  the game unless the user explicitly asks for that runtime action.
- Computer Use has no global hard cap, but the product workflow must not depend
  on Codex being online. Convert repeated exploration into ADB or MAA scripts,
  record every Computer Use operation, and keep it for first exploration,
  critical-node verification, and exception recovery.
- Do not use MAA's updater for this fork. Rebase onto the upstream version and
  rebuild the extension.
- Keep the packaged `config/`, `data/`, `debug/`, and `cache/` directories intact.
  Do not clean or overwrite them as part of a routine source change.

## Verification

- Python-only changes: `test-python`.
- C#/XAML changes: `test-dotnet`, then `build-gui` when MAA is closed.
- Core or task-resource changes: Debug runtime validation, not Release.
- Endpoint or parser changes: add contract tests for loopback enforcement, exact
  stage matching, ordering, deduplication, and count limits.
