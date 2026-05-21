# Security Policy

## Supported versions

Retrace is under active development. Security fixes will generally target the latest released version and the main branch.

We do not currently commit to supporting older versions unless explicitly stated in a release note or security advisory.

## Reporting a vulnerability

Please do not report security vulnerabilities in public GitHub issues, pull requests, or discussions.

Email security reports to:

security@retracesoftware.com

Please include:

- A clear description of the issue.
- Steps to reproduce, if possible.
- The affected version or commit.
- Any relevant logs, stack traces, or proof-of-concept details.
- Whether you believe the issue is already being exploited.

Please do not include production trace files, secrets, credentials, customer data, or other sensitive material in your initial report.

We will review reports on a best-efforts basis and may follow up for additional information.

## Sensitive trace data

Retrace trace files may contain sensitive information from a recorded execution, including application data, request data, environment-derived values, database results, file paths, secrets, tokens, customer data, or other confidential information.

Do not upload trace files from production, customer, or confidential environments to public GitHub issues, pull requests, discussions, or other public forums.

If a trace file is needed to investigate a security issue, contact us first so we can agree a safe way to share it.

## Untrusted trace files

Treat trace files as potentially sensitive and potentially unsafe.

Do not open, replay, or inspect trace files from untrusted sources unless you understand the risk and are using an appropriate isolated environment.

## Scope

Examples of security issues we are interested in:

- Leaking secrets or sensitive data unexpectedly.
- Unsafe handling, storage, parsing, or replay of trace files.
- Replay behaviour that causes unintended external side effects.
- Boundary, proxy, or gate failures that allow recorded or replayed execution to behave incorrectly.
- Vulnerabilities in the debugger, replay, proxy, trace-reading, or serialization paths.
- Supply-chain, dependency, build, or packaging issues that could affect users.

Out of scope:

- Social engineering.
- Denial-of-service attacks against project infrastructure.
- Reports requiring access to a user's private machine, repository, credentials, or trace file without a separate vulnerability.
- Vulnerabilities in third-party applications recorded or replayed with Retrace, unless Retrace itself introduces or exposes the issue.

## Public disclosure

Please give us a reasonable opportunity to investigate and address security reports before public disclosure.
