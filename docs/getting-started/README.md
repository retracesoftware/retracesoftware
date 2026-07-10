# Getting Started

These guides cover the public workflow for a new Retrace user.

The included pytest quickstart takes about 5 minutes. Before starting it,
check that Python 3.12 and Git are installed. The guides show how to confirm
the Retrace packages are installed, record a failed pytest run, generate an AI
debugger report, and optionally open the `.retrace` recording in VS Code.

Read them in order:

1. [Installation](installation.md)
2. [Quickstart](../../quickstart/README.md)
3. [Recording Python Commands](recording-python-commands.md)
4. [VS Code Extension](vscode-extension.md)

The current recommended pytest AI-debugger flow is:

```
cd /path/to/your/project
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --upgrade retracesoftware
python -m pip show retracesoftware
python -m pip show retracesoftware-dap
mkdir -p recordings
export RETRACE_AUTO_DEBUG=1
retracepython --recording recordings/pytest.retrace -m pytest tests
```

If pytest fails, Retrace writes `recordings/pytest.retrace` and
`recordings/pytest.ai-report.md`.

For the bundled demo and VS Code replay walkthrough:

```
git clone https://github.com/retracesoftware/retracesoftware.git
cd retracesoftware/quickstart
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install retracesoftware
python -m pip install -r requirements.txt
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m retracesoftware --recording recordings/pytest.retrace -- -m pytest pytest_demo -q --tb=short
code .
```

Then install the Retrace VS Code extension from the Marketplace and open the
`.retrace` recording from VS Code. Add breakpoints in your source files and
start the Retrace debug configuration to replay the recorded execution.

Terminal replay is also available when you want a quick sanity check:

```
./recordings/pytest.retrace --extract
ROOT_PID=$(python -m retracesoftware --recording recordings/pytest.retrace --list_pids | head -1)
./recordings/pytest.d/${ROOT_PID}.bin
```
