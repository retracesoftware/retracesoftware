import * as vscode from "vscode";
import { RetraceDebugAdapterFactory } from "./debugAdapter";
import { ProcessTreeProvider, Process } from "./processTree";
import { openRecordingWorkspace } from "./workspace";
import { log } from "./log";

let treeProvider: ProcessTreeProvider;

export function activate(context: vscode.ExtensionContext): void {
  treeProvider = new ProcessTreeProvider();

  context.subscriptions.push(
    vscode.debug.registerDebugAdapterDescriptorFactory(
      "retrace",
      new RetraceDebugAdapterFactory(),
    ),
  );

  context.subscriptions.push(
    vscode.debug.registerDebugAdapterTrackerFactory("retrace", {
      createDebugAdapterTracker() {
        return {
          onWillStartSession() {
            log("DAP session starting");
          },
          onWillReceiveMessage(msg: unknown) {
            log(`DAP >>> ${JSON.stringify(msg)}`);
          },
          onDidSendMessage(msg: unknown) {
            log(`DAP <<< ${JSON.stringify(msg)}`);
          },
          onError(err: Error) {
            log(`DAP error: ${err.message}`);
          },
          onWillStopSession() {
            log("DAP session stopping");
          },
        };
      },
    }),
  );

  context.subscriptions.push(
    vscode.window.createTreeView("retrace.sidebar", {
      treeDataProvider: treeProvider,
      showCollapseAll: true,
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("retrace.openRecording", openRecording),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(
      "retrace.openRecordingFromFile",
      openRecordingFromFile,
    ),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(
      "retrace.replayProcess",
      replayProcess,
    ),
  );

  autoLoadFromWorkspaceSettings();
}

async function openRecording(): Promise<void> {
  const uris = await vscode.window.showOpenDialog({
    canSelectFiles: true,
    canSelectMany: false,
    filters: { "Retrace recordings": ["retrace"] },
    openLabel: "Open Recording",
  });

  if (!uris || uris.length === 0) return;

  await loadRecording(uris[0].fsPath);
}

async function openRecordingFromFile(uri: vscode.Uri): Promise<void> {
  await loadRecording(uri.fsPath);
}

async function loadRecording(recording: string): Promise<void> {
  try {
    const hasWorkspace =
      vscode.workspace.workspaceFolders &&
      vscode.workspace.workspaceFolders.length > 0;

    if (hasWorkspace) {
      await treeProvider.loadRecording(recording);
    } else {
      await openRecordingWorkspace(recording);
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    vscode.window.showErrorMessage(`Failed to open recording: ${msg}`);
  }
}

async function replayProcess(process: Process): Promise<void> {
  const recording = treeProvider.getRecordingPath();
  if (!recording) return;

  await vscode.debug.startDebugging(undefined, {
    type: "retrace",
    request: "launch",
    name: `Retrace: PID ${process.pid}`,
    recording,
    pid: process.pid,
  });
}

async function autoLoadFromWorkspaceSettings(): Promise<void> {
  const config = vscode.workspace.getConfiguration("retrace");
  const recording = config.get<string>("recording");
  if (!recording) return;

  try {
    await treeProvider.loadRecording(recording);
  } catch {
    // Silently ignore -- the recording may no longer exist
  }
}

export function deactivate(): void {}
