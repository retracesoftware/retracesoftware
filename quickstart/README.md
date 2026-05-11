# Retrace Quickstart

This is a minimal quickstart for trying Retrace with a small Flask example. It
takes about 5 minutes.

Before you start, make sure you have:

1. Python 3.12 (`python3.12 --version`)
2. Go 1.25 or newer (`go version`)
3. Git
4. VS Code for replay debugging

See [../COMPATIBILITY.md](../COMPATIBILITY.md) for current platform details.

By the end you will have a `.retrace` recording of a small Flask app and a VS
Code session that can step backward from a breakpoint inside that recording.

## Requirements

- Python 3.12
- Go 1.25 or newer
- Git
- VS Code

## 1. Clone The Repo

```
git clone https://github.com/retracesoftware/retracesoftware.git
cd retracesoftware/quickstart
```

## 2. Check Go

Retrace installs with `pip`, but replay extraction and VS Code
replay/debugging use Retrace's Go replay tool. Check that Go is available:

```
go version
```

If that command fails, install Go before continuing.

On macOS with Homebrew:

```
brew install go
```

On Linux, install Go 1.25 or newer from your distro packages or from
[go.dev/dl](https://go.dev/dl/).

## What Is In This Folder

```
quickstart/
  examples/
    flask_demo.py
    simple_demo.py
  recordings/
  README.md
  requirements.txt
```

The main demo is:

```
examples/flask_demo.py
```

It defines a small Flask app, calls it with Flask's in-process test client, and
prints the responses. It does not start a real network server, so there is no
port to manage.

The demo intentionally uses values that normally change between runs:

- current time
- UUIDs
- random numbers

That makes it easy to see what Retrace records and replays.

## 3. Create A Python 3.12 Virtual Environment

Check that Python 3.12 is available:

```
python3.12 --version
```

Create the virtual environment:

```
python3.12 -m venv .venv
```

Activate it:

```
source .venv/bin/activate
```

After activation, your terminal prompt should start with:

```
(.venv)
```

## 4. Install Retrace

Install Retrace from PyPI:

```
python -m pip install --upgrade pip
python -m pip install retracesoftware
```

That installs the Retrace Python package into this virtual environment.

Check that the installation worked before continuing:

```
python -m pip show retracesoftware
```

You should see package details that include:

```
Name: retracesoftware
Version: ...
```

## 5. Enable Auto-Recording In This Virtual Environment

Install Retrace's auto-enable hook:

```
python -m retracesoftware install
```

This adds a small `.pth` file to the active virtual environment. After that,
fresh Python processes can start under Retrace automatically when you provide
the `RETRACE_RECORDING` environment variable.

This command does not record anything by itself.

## 6. Install The Flask Demo Dependency

Install the demo dependency from `requirements.txt`:

```
python -m pip install -r requirements.txt
```

The quickstart app dependency is intentionally separate from Retrace so the
Retrace install stays obvious:

```
flask>=3.0
```

## 7. Run The Flask Demo Normally

```
python examples/flask_demo.py
```

You should see output like:

```
=== Retrace Flask demo ===
GET /health: status=200 body=...
POST /users Ada: status=201 body=...
POST /users Grace: status=201 body=...
GET /users/1: status=200 body=...
GET /summary: status=200 body=...
Flask demo complete.
```

This is a normal Python run. Nothing has been recorded yet.

## 8. Record The Flask Demo With Retrace

Run the same Python file, but put `RETRACE_RECORDING=...` before the command:

```
RETRACE_RECORDING=recordings/flask.retrace python examples/flask_demo.py
```

This creates:

```
recordings/flask.retrace
```

That `.retrace` file is the recording.

Check that the recording was written:

```
ls -lh recordings/flask.retrace
```

`recordings/` is just the folder where this quickstart stores generated
recordings. Retrace creates the folder automatically if it does not already
exist. `flask.retrace` is just the filename we chose for the Flask demo.

Use uppercase `RETRACE_RECORDING`. Lowercase `retrace_recording` will not enable
recording on macOS or Linux.

## 9. Install The VS Code Extension

Open VS Code.

Go to the Extensions sidebar and search for:

```
Retrace Debug Extension
```

Install the extension published by:

```
RetraceSoftware
```

Restart VS Code if it asks you to.

## 10. Open This Folder In VS Code

From this folder:

```
code .
```

If `code` is not available, open VS Code manually and choose:

```
File -> Open Folder...
```

Then select the `quickstart` folder.

## 11. Open The Recording In VS Code

Make sure this file exists first:

```
recordings/flask.retrace
```

If it does not exist, run the recording command from step 8 again.

Then in VS Code:

1. Open the Retrace sidebar.
2. Click `Open Recording...`.
3. Select:

```
recordings/flask.retrace
```

You can also right-click the `.retrace` file in the Explorer and choose:

```
Open as Retrace Recording
```

## 12. Replay And Debug

Open the source file:

```
examples/flask_demo.py
```

Add a breakpoint in one of these places:

- inside the `/health` route
- inside the `/users` route
- inside `main()`

Then use the Retrace sidebar to start replaying the recorded process.

During replay, VS Code should stop on your breakpoint. You can inspect local
variables, step forward, step backward, and continue through the recorded
execution.

You are done when VS Code stops at your breakpoint, the Retrace sidebar shows
the recorded process tree, and the Step Back button moves backward through the
recording. From there you can inspect variables, continue, step backward and
forward, reverse, and restart without rerunning the Flask demo live.

## Optional: Replay The Recording In The Terminal

Terminal replay is useful as a quick check that the recording itself is good.

First extract the replay files:

```
./recordings/flask.retrace --extract
```

This creates:

```
recordings/flask.d/
```

Find the recorded process id:

```
ROOT_PID=$(python -m retracesoftware --recording recordings/flask.retrace --list_pids | head -1)
echo "ROOT_PID=$ROOT_PID"
```

Replay the recorded process:

```
./recordings/flask.d/${ROOT_PID}.bin
```

The replay should print the same recorded Flask responses. Values like time,
UUIDs, and random numbers should match the recording instead of changing live.

## Optional: Try The Smaller Demo

Run normally:

```
python examples/simple_demo.py
```

Record:

```
RETRACE_RECORDING=recordings/simple.retrace python examples/simple_demo.py
```

Extract:

```
./recordings/simple.retrace --extract
```

Find the process id:

```
ROOT_PID=$(python -m retracesoftware --recording recordings/simple.retrace --list_pids | head -1)
echo "ROOT_PID=$ROOT_PID"
```

Replay:

```
./recordings/simple.d/${ROOT_PID}.bin
```

## Reset Recordings

```
rm -f recordings/*.retrace
rm -rf recordings/*.d
```

## Troubleshooting

### `python3.12: command not found`

Install Python 3.12 first, then create the virtual environment again:

```
python3.12 -m venv .venv
```

### `code: command not found`

Open VS Code manually and choose:

```
File -> Open Folder...
```

### Permission denied when running the recording

Run:

```
chmod +x recordings/flask.retrace
```

Then try the replay command again.

### Python version mismatch

Record and replay with the same virtual environment. If you recorded with
Python 3.12, replay with the same Python 3.12 environment.

### Recording did not create a `.retrace` file

Make sure you already ran:

```
python -m retracesoftware install
```

Then record with uppercase `RETRACE_RECORDING`:

```
RETRACE_RECORDING=recordings/flask.retrace python examples/flask_demo.py
```
