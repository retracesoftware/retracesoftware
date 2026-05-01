import * as vscode from "vscode";
import { execFile } from "child_process";
import { promisify } from "util";
import { replayBinaryFromTrace } from "./trace";
import { log } from "./log";

const execFileAsync = promisify(execFile);

/**
 * Call `replay --workspace` to generate a .code-workspace file
 * alongside the trace, then open it in the current window.
 */
export async function openRecordingWorkspace(
  recordingPath: string,
): Promise<void> {
  const binary = await replayBinaryFromTrace(recordingPath);
  const args = ["--recording", recordingPath, "--workspace"];
  log(`Workspace: ${binary} ${args.join(" ")}`);
  const { stdout } = await execFileAsync(binary, args);

  const workspacePath = stdout.trim();
  const uri = vscode.Uri.file(workspacePath);
  await vscode.commands.executeCommand("vscode.openFolder", uri, {
    forceNewWindow: false,
  });
}
