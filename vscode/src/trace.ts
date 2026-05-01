import { open } from "fs/promises";

/**
 * Read the shebang line from a trace file to extract the replay binary path.
 * Trace files start with `#!/path/to/replay --recording\n` followed by binary data.
 */
export async function replayBinaryFromTrace(
  tracePath: string,
): Promise<string> {
  const fh = await open(tracePath, "r");
  try {
    const buf = Buffer.alloc(4096);
    const { bytesRead } = await fh.read(buf, 0, buf.length, 0);
    const head = buf.subarray(0, bytesRead).toString("ascii");

    if (!head.startsWith("#!")) {
      throw new Error(`Trace file has no shebang: ${tracePath}`);
    }

    const newline = head.indexOf("\n");
    if (newline === -1) {
      throw new Error(`Malformed shebang in trace file: ${tracePath}`);
    }

    // Shebang format: #!/path/to/replay --recording
    // We want just the binary path (first token after #!).
    const shebangLine = head.substring(2, newline).trim();
    const binary = shebangLine.split(/\s+/)[0];
    if (!binary) {
      throw new Error(`Empty shebang in trace file: ${tracePath}`);
    }

    return binary;
  } finally {
    await fh.close();
  }
}
