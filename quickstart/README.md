# Retrace Quickstart

This is a minimal quickstart for trying Retrace with a small Flask example.

The goal is simple:

1. Clone the Retrace repo.
2. Open the `quickstart` folder.
3. Create a Python 3.12 virtual environment.
4. Install Retrace with one `pip` command.
5. Enable Retrace auto-recording for that virtual environment.
6. Install the Flask demo dependency.
7. Run the Flask demo normally.
8. Record the Flask demo by running ordinary Python with `RETRACE_RECORDING`.
9. Install the Retrace VS Code extension from the Marketplace.
10. Open the recording in VS Code.
11. Add breakpoints, step forward, and step backward through the recorded execution.

## Requirements

- Python 3.12
- Git
- VS Code

## 1. Clone The Repo

```
git clone https://github.com/retracesoftware/retracesoftware.git
cd retracesoftware/quickstart
```

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

## 2. Create A Python 3.12 Virtual Environment

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

## 3. Install Retrace

Install Retrace from PyPI:

```
python -m pip install --upgrade pip
python -m pip install retracesoftware
```

That installs the Retrace Python package into this virtual environment.

Check the installed package version:

```
python -m pip show retracesoftware
```

Optional: check that the Retrace CLI is available:

```
python -m retracesoftware --help
```

This command does not record anything. It just prints the Retrace command-line
options, which confirms that Python can find the installed package.

## 4. Enable Auto-Recording In This Virtual Environment

Install Retrace's auto-enable hook:

```
python -m retracesoftware install
```

This adds a small `.pth` file to the active virtual environment. After that,
fresh Python processes can start under Retrace automatically when you provide
the `RETRACE_RECORDING` environment variable.

This command does not record anything by itself.

## 5. Install The Flask Demo Dependency

Install the demo dependency from `requirements.txt`:

```
python -m pip install -r requirements.txt
```

The quickstart app dependency is intentionally separate from Retrace so the
Retrace install stays obvious:

```
flask>=3.0
```

## 6. Run The Flask Demo Normally

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

## 7. Record The Flask Demo With Retrace

Run the same Python file, but put `RETRACE_RECORDING=...` before the command:

```
RETRACE_RECORDING=recordings/flask.retrace python examples/flask_demo.py
```

This creates:

```
recordings/flask.retrace
```

That `.retrace` file is the recording.

`recordings/` is just the folder where this quickstart stores generated
recordings. Retrace creates the folder automatically if it does not already
exist. `flask.retrace` is just the filename we chose for the Flask demo.

Use uppercase `RETRACE_RECORDING`. Lowercase `retrace_recording` will not enable
recording on macOS or Linux.

## 8. Replay The Recording In The Terminal

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

If it does not exist, run the recording command from step 7 again.

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
