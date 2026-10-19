"""Small helpers shared by the tests."""
import json
import os
import subprocess
import sys
from pathlib import Path


def run_hook(script, args, data, cwd=None, env=None):
    """Run the hook script the way an agent does: python -S -I <script> <args>, payload on stdin."""
    return subprocess.run([sys.executable, "-S", "-I", str(script)] + [str(a) for a in args], input=data,
                          capture_output=True, timeout=60, cwd=cwd, env=env)


def spool_lines(spool):
    """(file name, parsed line) for every line of every spool file, in file-name order."""
    out = []
    for path in sorted(p for p in Path(spool).rglob("*.jsonl") if p.is_file()):
        for line in path.read_bytes().splitlines():
            out.append((path.name, json.loads(line)))
    return out


def tree(folder):
    return sorted(p.relative_to(folder).as_posix() for p in Path(folder).rglob("*"))


def symlinks_work(folder):
    probe = Path(folder) / "probe-link"
    try:
        os.symlink(folder, probe, target_is_directory=True)
        os.unlink(probe)
        return True
    except (OSError, NotImplementedError):
        return False


def spool_event(state_root, session, event, cwd, agent="claude", ts=None, **fields):
    """Append one spool line the way the hook script writes it (no hook process)."""
    import time
    line = {"ts": ts if ts is not None else time.time_ns(), "pid": 1, "agent": agent, "event": event,
            "len": 1, "session_id": session, "hook_event_name": event, "cwd": str(cwd)}
    line.update(fields)
    path = Path(state_root) / "spool" / agent / (session + ".jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as fh:
        fh.write(json.dumps(line).encode("utf-8") + b"\n")
    return path


def hook_payload(event, session, cwd, **fields):
    body = {"session_id": session, "transcript_path": "/t/x.jsonl", "cwd": str(cwd), "hook_event_name": event}
    body.update(fields)
    return json.dumps(body).encode("utf-8")
