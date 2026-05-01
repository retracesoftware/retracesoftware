import * as vscode from "vscode";
import { replayBinaryFromTrace } from "./trace";
import { log } from "./log";

interface RetraceLaunchConfig extends vscode.DebugConfiguration {
  recording: string;
  pid?: number;
  replayBinary?: string;
}

export class RetraceDebugAdapterFactory
  implements vscode.DebugAdapterDescriptorFactory
{
  async createDebugAdapterDescriptor(
    session: vscode.DebugSession,
  ): Promise<vscode.DebugAdapterDescriptor> {
    const config = session.configuration as RetraceLaunchConfig;

    const binary =
      config.replayBinary || (await replayBinaryFromTrace(config.recording));

    const args = ["--recording", config.recording, "--dap"];
    if (config.pid) {
      args.push("--pid", String(config.pid));
    }

    const env: Record<string, string> = {};
    if (vscode.workspace.getConfiguration("retrace").get<boolean>("debugProtocol")) {
      env.RETRACE_DEBUG_PROTOCOL = "1";
      log("Protocol logging enabled (RETRACE_DEBUG_PROTOCOL=1)");
    }

    log(`Debug adapter: ${binary} ${args.join(" ")}`);

    const options: vscode.DebugAdapterExecutableOptions = {
      env: { ...process.env, ...env },
    };
    return new vscode.DebugAdapterExecutable(binary, args, options);
  }
}
