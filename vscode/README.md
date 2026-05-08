# Retrace VS Code Extension

This directory contains the Retrace VS Code extension.

The extension is a thin TypeScript client. It handles VS Code UI, command
registration, recording selection, process tree display, and debug launch
wiring. Replay, stepping, breakpoint, stack-frame, and variable semantics live
in the Go replay tooling and Python replay runtime.

## Marketplace Install

For normal use, install the Marketplace extension:

```
Retrace Debug Extension
```

Publisher:

```
RetraceSoftware
```

Then open a folder that contains a `.retrace` recording and its source files.
Use the Retrace sidebar or right-click a `.retrace` file and choose
`Open as Retrace Recording`.

## Development

Install dependencies:

```
npm install
```

Build the extension bundle:

```
npm run build
```

Watch during development:

```
npm run watch
```

Package a VSIX:

```
npm run package
```

For a manual extension-host session, open this `vscode/` directory in VS Code
and run the `Run Extension` launch configuration.

## Boundaries

Keep DAP behavior in `go/replay/` and Python replay/control behavior in
`src/retracesoftware/control_runtime.py`. The TypeScript extension should stay
focused on VS Code integration and debug-adapter process startup.
