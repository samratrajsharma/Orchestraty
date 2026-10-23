"""The user's own command, such as their tests, run with a time limit.

The command is the user's, a line for the system's shell, run in the folder given with no input,
its output and errors captured together, and an environment without git's variables (an outer
git's ``GIT_DIR`` and the rest would point git inside the command at another repository). When
the time runs out, the command and everything it started are stopped: on POSIX the command runs
in a session of its own, which is killed whole; on Windows, in a process group of its own,
which ``taskkill /T`` ends. No console window opens on Windows. What comes back is the exit
code (None on a timeout), the seconds it took and the last lines of its output (and, when asked,
the first ones), each cut to a length and with control characters taken out: output is the
command's, and anything may be in it, so it is shown only as that.
"""
import os
import signal
import subprocess
import time

MAX_LINES = 20
MAX_LINE = 300
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def environment(base=None):
    """The environment a user's command runs in: this one, without git's variables."""
    return {k: v for k, v in (os.environ if base is None else base).items() if not k.upper().startswith("GIT_")}


def clean_line(text, limit=MAX_LINE):
    """One line of a command's output, safe to show: no control characters, cut to `limit`."""
    text = "".join(c if c >= " " and c != "\x7f" else " " for c in text.replace("\t", "    ")).rstrip()
    return text if len(text) <= limit else text[:limit - 1] + "~"


def last_lines(data, count=MAX_LINES):
    """The last non-empty lines of a command's output (bytes), cleaned."""
    lines = [clean_line(x) for x in data.decode("utf-8", "replace").splitlines()]
    return [x for x in lines if x.strip()][-count:]


def first_lines(data, count=MAX_LINES):
    """The first non-empty lines of a command's output (bytes), cleaned."""
    lines = [clean_line(x) for x in data.decode("utf-8", "replace").splitlines()]
    return [x for x in lines if x.strip()][:count]


def _stop(proc):
    """Stop a command that ran out of time, and everything it started."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=30,
                           creationflags=_NO_WINDOW)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def run(command, cwd, timeout, env=None, first=0):
    """{"command", "exit_code" (None when it timed out), "timed_out", "seconds", "last_lines"};
    with `first`, also "first_lines", the first that many non-empty lines, cleaned."""
    started = time.monotonic()
    kwargs = {"creationflags": _NO_WINDOW | _GROUP} if os.name == "nt" else {"start_new_session": True}
    try:
        proc = subprocess.Popen(command, shell=True, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=environment() if env is None else env, **kwargs)
    except OSError as exc:
        return {"command": command, "exit_code": None, "timed_out": False, "seconds": 0.0,
                "last_lines": [clean_line(f"could not start: {type(exc).__name__}")]}
    timed_out = False
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _stop(proc)
        try:
            out, _ = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:           # a child kept the output open: give up on it
            out = b""
    result = {"command": command, "exit_code": None if timed_out else proc.returncode, "timed_out": timed_out,
              "seconds": round(time.monotonic() - started, 1), "last_lines": last_lines(out or b"")}
    if first:
        result["first_lines"] = first_lines(out or b"", first)
    return result
