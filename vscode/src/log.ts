import * as vscode from "vscode";

const channel = vscode.window.createOutputChannel("Retrace");

export function log(message: string): void {
  const ts = new Date().toISOString().slice(11, 23);
  channel.appendLine(`[${ts}] ${message}`);
}
