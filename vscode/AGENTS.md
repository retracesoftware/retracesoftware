# VS Code Extension

This directory contains the Retrace VS Code extension. The extension is a thin
TypeScript client for the Go replay binary's DAP adapter; DAP semantics live in
`go/replay/`, and Python replay/control behavior lives in
`src/retracesoftware/control_runtime.py`.

## Working Rules

- Keep the extension focused on VS Code UI, command registration, process tree
  display, launch wiring, and debug-adapter process startup.
- Do not duplicate DAP stepping, breakpoint, stack-frame, or variable semantics
  in TypeScript. Those belong in `go/replay/` and the Python control runtime.
- If extension behavior changes the DAP launch path or user-visible debug
  workflow, update `docs/DEBUGGER_DESIGN.md` and the opt-in VS Code smoke test
  in `tests/test_vscode_e2e_smoke.py`.
- Do not commit `node_modules/`, `dist/`, or packaged `.vsix` files. Build
  artifacts are regenerated from the TypeScript sources.

## Commands

- Install dependencies: `cd vscode && npm install`
- Build extension bundle: `cd vscode && npm run build`
- Watch during extension development: `cd vscode && npm run watch`
- Package VSIX: `cd vscode && npm run package`
