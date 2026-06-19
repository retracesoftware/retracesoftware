"""Opt-in end-to-end smoke for installed Retrace plus VS Code debugging.

This test intentionally sits at the outer edge of the project:

1. build a wheel from the checkout;
2. install it into a fresh virtualenv;
3. make that virtualenv Retrace-aware;
4. record a script by running its Python with RETRACE_RECORDING set;
5. open the generated workspace in VS Code and drive a real ``retrace`` debug
   session through VS Code's debug API.

It is skipped unless ``RETRACE_RUN_VSCODE_E2E=1`` is set because it launches
VS Code/Electron and requires a built Retrace VS Code extension.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


VSCODE_DRIVER = r"""
const fs = require("fs");
const vscode = require("vscode");

const recording = process.env.RETRACE_VSCODE_SMOKE_RECORDING;
const target = process.env.RETRACE_VSCODE_SMOKE_TARGET;
const breakpointLine = Number(process.env.RETRACE_VSCODE_SMOKE_LINE || "0");
const resultPath = process.env.RETRACE_VSCODE_SMOKE_RESULT;

function writeResult(payload) {
  fs.writeFileSync(resultPath, JSON.stringify(payload, null, 2));
}

function waitFor(predicate, label, timeoutMs) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const timer = setInterval(() => {
      try {
        const value = predicate();
        if (value) {
          clearInterval(timer);
          resolve(value);
          return;
        }
        if (Date.now() - started > timeoutMs) {
          clearInterval(timer);
          reject(new Error(`Timed out waiting for ${label}`));
        }
      } catch (err) {
        clearInterval(timer);
        reject(err);
      }
    }, 100);
  });
}

async function run() {
  const messages = [];
  let sessionFromTracker = undefined;

  const tracker = vscode.debug.registerDebugAdapterTrackerFactory("retrace", {
    createDebugAdapterTracker(session) {
      sessionFromTracker = session;
      return {
        onWillReceiveMessage(message) {
          messages.push({ direction: "toAdapter", message });
        },
        onDidSendMessage(message) {
          messages.push({ direction: "fromAdapter", message });
        },
        onError(error) {
          messages.push({
            direction: "error",
            message: error && error.stack ? error.stack : String(error),
          });
        },
      };
    },
  });

  try {
    if (!recording || !target || !breakpointLine || !resultPath) {
      throw new Error("Missing RETRACE_VSCODE_SMOKE_* environment");
    }

    const extension = vscode.extensions.getExtension("retracesoftware.retrace");
    if (!extension) {
      throw new Error("Retrace VS Code extension retracesoftware.retrace is not loaded");
    }
    await extension.activate();

    const targetUri = vscode.Uri.file(target);
    const document = await vscode.workspace.openTextDocument(targetUri);
    await vscode.window.showTextDocument(document);

    vscode.debug.removeBreakpoints(vscode.debug.breakpoints);
    vscode.debug.addBreakpoints([
      new vscode.SourceBreakpoint(
        new vscode.Location(targetUri, new vscode.Position(breakpointLine - 1, 0)),
        true,
      ),
    ]);

    const started = await vscode.debug.startDebugging(
      vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0],
      {
        type: "retrace",
        request: "launch",
        name: "Retrace VS Code smoke",
        recording,
      },
    );
    if (!started) {
      throw new Error("vscode.debug.startDebugging returned false");
    }

    await waitFor(
      () =>
        messages.find((entry) => {
          const message = entry.message || {};
          return (
            entry.direction === "fromAdapter" &&
            message.type === "event" &&
            message.event === "stopped" &&
            message.body &&
            message.body.reason === "entry"
          );
        }),
      "entry stopped event",
      60000,
    );

    const session = vscode.debug.activeDebugSession || sessionFromTracker;
    if (!session) {
      throw new Error("No active VS Code debug session after entry stop");
    }

    await session.customRequest("continue", { threadId: 1 });

    await waitFor(
      () =>
        messages.find((entry) => {
          const message = entry.message || {};
          return (
            entry.direction === "fromAdapter" &&
            message.type === "event" &&
            message.event === "stopped" &&
            message.body &&
            message.body.reason === "breakpoint"
          );
        }),
      "breakpoint stopped event",
      60000,
    );

    const stack = await session.customRequest("stackTrace", {
      threadId: 1,
      startFrame: 0,
      levels: 5,
    });
    const frames = stack && stack.stackFrames;
    if (!Array.isArray(frames) || frames.length === 0) {
      throw new Error(`stackTrace returned no frames: ${JSON.stringify(stack)}`);
    }

    const top = frames[0];
    const sourcePath = top.source && top.source.path;
    if (sourcePath !== target || top.line !== breakpointLine) {
      throw new Error(
        `top frame mismatch: got ${JSON.stringify(top)}, want ${target}:${breakpointLine}`,
      );
    }

    writeResult({
      ok: true,
      topFrame: top,
      messageCount: messages.length,
    });

    await vscode.debug.stopDebugging(session);
  } catch (error) {
    writeResult({
      ok: false,
      error: error && error.stack ? error.stack : String(error),
      messages,
    });
    throw error;
  } finally {
    tracker.dispose();
    await vscode.commands.executeCommand("workbench.action.closeWindow").then(
      () => undefined,
      () => undefined,
    );
  }
}

module.exports = { run };
"""


def run(
    args: list[str | Path],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    assert proc.returncode == 0, (
        f"command failed ({proc.returncode}): {' '.join(map(str, args))}\n"
        f"stdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    return proc


def python_version(python: Path) -> tuple[int, int]:
    proc = run(
        [
            python,
            "-c",
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
        ],
        timeout=30,
    )
    major, minor = proc.stdout.strip().split(".")
    return int(major), int(minor)


def find_python312() -> Path | None:
    candidates: list[str | Path | None] = [
        os.environ.get("RETRACE_VSCODE_E2E_PYTHON"),
        ROOT / ".venv312" / "bin" / "python",
        shutil.which("python3.12"),
        sys.executable,
    ]

    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            if python_version(path) >= (3, 12):
                return path
        except Exception:
            continue
    return None


def venv_python(venv: Path) -> Path:
    bin_dir = "Scripts" if os.name == "nt" else "bin"
    return venv / bin_dir / "python"


def clean_record_env(replay_bin: Path, trace: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("RETRACE_") or key in {
            "MESONPY_EDITABLE_SKIP",
            "PYTHONPATH",
            "VIRTUAL_ENV",
        }:
            env.pop(key, None)
    env.update(
        {
            "RETRACE_CONFIG": "debug",
            "RETRACE_RECORDING": str(trace),
            "RETRACE_REPLAY_BIN": str(replay_bin),
        }
    )
    return env


def wait_for_json(path: Path, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for JSON result: {path}")


def find_vscode_extension() -> Path | None:
    explicit = os.environ.get("RETRACE_VSCODE_EXTENSION_PATH")
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))

    candidates.extend(
        [
            ROOT / "vscode",
            Path.home() / ".vscode" / "extensions" / "retracesoftware.retrace-0.0.1",
            Path.home() / ".cursor" / "extensions" / "retracesoftware.retrace-0.0.1",
        ]
    )

    for pattern in (
        Path.home() / ".vscode" / "extensions" / "retracesoftware.retrace-*",
        Path.home() / ".cursor" / "extensions" / "retracesoftware.retrace-*",
    ):
        candidates.extend(sorted(pattern.parent.glob(pattern.name), reverse=True))

    for candidate in candidates:
        package_json = candidate / "package.json"
        if not package_json.exists():
            continue
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if package.get("publisher") != "retracesoftware" or package.get("name") != "retrace":
            continue
        main = candidate / package.get("main", "")
        if main.exists():
            return candidate
    return None


def test_fresh_install_record_and_vscode_debug_smoke(tmp_path: Path):
    if os.environ.get("RETRACE_RUN_VSCODE_E2E") != "1":
        pytest.skip("set RETRACE_RUN_VSCODE_E2E=1 to launch the VS Code smoke test")

    python = find_python312()
    if python is None:
        pytest.skip("VS Code smoke requires Python 3.12+; set RETRACE_VSCODE_E2E_PYTHON")

    code = os.environ.get("RETRACE_VSCODE_BIN") or shutil.which("code")
    if not code:
        pytest.skip("VS Code CLI not found; set RETRACE_VSCODE_BIN")

    extension = find_vscode_extension()
    if extension is None:
        pytest.skip("Retrace VS Code extension not found; set RETRACE_VSCODE_EXTENSION_PATH")

    go_src = tmp_path / "go-src"
    shutil.copytree(
        ROOT / "go",
        go_src,
        ignore=shutil.ignore_patterns("*.test", "__pycache__"),
    )
    replay_bin = tmp_path / "replay"
    run(["go", "build", "-o", replay_bin, "./cmd/replay"], cwd=go_src, timeout=120)

    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    run(
        [
            python,
            "-m",
            "pip",
            "wheel",
            ".",
            "--wheel-dir",
            wheelhouse,
            "--no-deps",
            "--no-build-isolation",
        ],
        timeout=240,
    )

    venv_dir = tmp_path / "runtime-venv"
    run([python, "-m", "venv", venv_dir], timeout=120)
    runtime_python = venv_python(venv_dir)
    run(
        [
            runtime_python,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            wheelhouse,
            "retracesoftware",
        ],
        timeout=120,
    )

    run(
        [
            runtime_python,
            "-m",
            "retracesoftware",
            "venv",
            str(venv_dir),
            "--without-pip",
        ],
        timeout=30,
    )

    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    target = workspace_dir / "target.py"
    target.write_text(
        textwrap.dedent(
            """\
            import time

            value = time.time()
            marker = "break-here"
            print(f"value={value} marker={marker}")
            """
        ),
        encoding="utf-8",
    )

    trace = workspace_dir / "target.retrace"
    record = run(
        [runtime_python, target],
        cwd=workspace_dir,
        env=clean_record_env(replay_bin, trace),
        timeout=120,
    )
    assert "marker=break-here" in record.stdout
    assert trace.exists() and trace.stat().st_size > 0

    workspace = run(
        [replay_bin, "--recording", trace, "--workspace"],
        cwd=workspace_dir,
        timeout=60,
    ).stdout.strip()
    workspace_path = Path(workspace)
    assert workspace_path.exists()

    driver = tmp_path / "vscode-driver.js"
    driver.write_text(VSCODE_DRIVER, encoding="utf-8")
    result = tmp_path / "vscode-result.json"

    vscode_env = os.environ.copy()
    vscode_env.update(
        {
            "RETRACE_VSCODE_SMOKE_RECORDING": str(trace),
            "RETRACE_VSCODE_SMOKE_TARGET": str(target),
            "RETRACE_VSCODE_SMOKE_LINE": "4",
            "RETRACE_VSCODE_SMOKE_RESULT": str(result),
        }
    )
    for key in list(vscode_env):
        keep_retrace_key = key in {
            "RETRACE_VSCODE_SMOKE_RECORDING",
            "RETRACE_VSCODE_SMOKE_TARGET",
            "RETRACE_VSCODE_SMOKE_LINE",
            "RETRACE_VSCODE_SMOKE_RESULT",
        }
        if key.startswith("RETRACE_") and not keep_retrace_key:
            vscode_env.pop(key, None)
    for key in ("MESONPY_EDITABLE_SKIP", "PYTHONPATH", "VIRTUAL_ENV"):
        vscode_env.pop(key, None)

    run(
        [
            code,
            "--user-data-dir",
            tmp_path / "vscode-user-data",
            "--extensions-dir",
            tmp_path / "vscode-extensions",
            "--extensionDevelopmentPath",
            extension,
            "--extensionTestsPath",
            driver,
            "--disable-workspace-trust",
            "--disable-gpu",
            "--skip-welcome",
            "--skip-release-notes",
            "--new-window",
            "--wait",
            "--sync",
            "off",
            workspace_path,
        ],
        env=vscode_env,
        timeout=180,
    )

    payload = wait_for_json(result)
    assert payload.get("ok") is True, json.dumps(payload, indent=2)
    assert payload["topFrame"]["source"]["path"] == str(target)
    assert payload["topFrame"]["line"] == 4
