import * as vscode from "vscode";
import { execFile } from "child_process";
import { promisify } from "util";
import * as path from "path";
import { replayBinaryFromTrace } from "./trace";
import { log } from "./log";

const execFileAsync = promisify(execFile);

export interface Segment {
  offset: number;
  size: number;
}

export interface Process {
  pid: number;
  type: string;
  parent_pid?: number;
  fork_index?: number;
  parent_offset?: number;
  preamble: Record<string, unknown>;
  segments: Segment[];
  children: Process[];
}

export interface TraceIndex {
  trace_file: string;
  root: Process;
}

/**
 * Run `replay --index` and return the parsed trace index without
 * updating any tree view state.
 */
export async function indexRecording(
  recordingPath: string,
): Promise<TraceIndex> {
  const binary = await replayBinaryFromTrace(recordingPath);
  const args = ["--recording", recordingPath, "--index"];
  log(`Index: ${binary} ${args.join(" ")}`);
  const { stdout } = await execFileAsync(binary, args);
  return JSON.parse(stdout) as TraceIndex;
}

export function workingDirectoriesFromIndex(index: TraceIndex): string[] {
  const cwds = new Set<string>();
  collectCwds(index.root, cwds);
  return [...cwds];
}

export class ProcessTreeProvider
  implements vscode.TreeDataProvider<Process>
{
  private index: TraceIndex | undefined;
  private recordingPath: string | undefined;

  private _onDidChangeTreeData = new vscode.EventEmitter<
    Process | undefined | void
  >();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  async loadRecording(recordingPath: string): Promise<void> {
    const binary = await replayBinaryFromTrace(recordingPath);
    const { stdout } = await execFileAsync(binary, [
      "--recording",
      recordingPath,
      "--index",
    ]);
    this.index = JSON.parse(stdout) as TraceIndex;
    this.recordingPath = recordingPath;
    this._onDidChangeTreeData.fire();
  }

  getRecordingPath(): string | undefined {
    return this.recordingPath;
  }

  getIndex(): TraceIndex | undefined {
    return this.index;
  }

  getWorkingDirectories(): string[] {
    if (!this.index) return [];
    const cwds = new Set<string>();
    collectCwds(this.index.root, cwds);
    return [...cwds];
  }

  getTreeItem(process: Process): vscode.TreeItem {
    const hasChildren = process.children.length > 0;
    const label = processLabel(process);
    const item = new vscode.TreeItem(
      label,
      hasChildren
        ? vscode.TreeItemCollapsibleState.Expanded
        : vscode.TreeItemCollapsibleState.None,
    );

    item.tooltip = `PID ${process.pid}`;
    item.contextValue = hasChildren ? "processParent" : "processLeaf";

    if (hasChildren) {
      item.iconPath = new vscode.ThemeIcon("git-merge");
    } else {
      item.iconPath = new vscode.ThemeIcon("debug-start");
      item.command = {
        command: "retrace.replayProcess",
        title: "Replay Process",
        arguments: [process],
      };
    }

    return item;
  }

  getChildren(element?: Process): Process[] {
    if (!this.index) return [];
    if (!element) return [this.index.root];
    return element.children;
  }
}

function collectCwds(process: Process, cwds: Set<string>): void {
  const cwd = process.preamble?.cwd;
  if (typeof cwd === "string") cwds.add(cwd);
  for (const child of process.children) collectCwds(child, cwds);
}

function processLabel(process: Process): string {
  const pid = process.pid;
  if (process.type === "exec") {
    const exe = process.preamble?.executable;
    if (typeof exe === "string") {
      return `${path.basename(exe)} (PID ${pid})`;
    }
    return `PID ${pid} (exec)`;
  }
  if (process.type === "fork") {
    const idx = process.fork_index ?? 0;
    return `fork #${idx} (PID ${pid})`;
  }
  return `PID ${pid}`;
}
