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


# Payloads in the shape Codex and Antigravity send (synthetic; Codex's keys in its order).
SECRET = "SECRET-CONTENT-5d1e"


def codex(event, session, cwd, **fields):
    """A Codex payload, its keys in the order Codex writes them."""
    body = {"session_id": session}
    if event not in ("SessionStart", "SessionEnd"):
        body["turn_id"] = "turn-1"
    body.update(transcript_path=None, cwd=str(cwd), hook_event_name=event)
    if event != "SessionEnd":
        body.update(model="model-1", permission_mode="default")
    body.update(fields)
    return json.dumps(body).encode("utf-8")


def patch_event(event, session, cwd, patch, **fields):
    extra = {"tool_response": {"output": SECRET}} if event == "PostToolUse" else {}
    return codex(event, session, cwd, tool_name="apply_patch", tool_input={"command": patch}, **extra,
                 tool_use_id="call_1", **fields)


def ag(event, conversation, folders, **fields):
    """An Antigravity payload."""
    body = {"conversationId": conversation, "workspacePaths": [str(f) for f in folders],
            "transcriptPath": "/t/brain/transcript.jsonl", "artifactDirectoryPath": "/t/brain/artifacts"}
    body.update(fields)
    return json.dumps(body).encode("utf-8")


def ag_tool(event, conversation, folder, name, args, args_first=False):
    call = {"args": args, "name": name} if args_first else {"name": name, "args": args}
    return ag(event, conversation, [folder, "/second/workspace"], toolCall=call, stepIdx=7)


def refuse_writes(monkeypatch, *paths):
    """Make os.replace onto these files fail, as it does on Windows when a file is read-only or
    another program has it open. Returns the set of blocked paths: clear it to let writes through."""
    blocked = {os.path.normcase(os.path.abspath(str(p))) for p in paths}
    real = os.replace

    def replace(src, dst, *args, **kwargs):
        if os.path.normcase(os.path.abspath(str(dst))) in blocked:
            raise PermissionError(13, "Access is denied", str(dst))
        return real(src, dst, *args, **kwargs)
    monkeypatch.setattr(os, "replace", replace)
    return blocked


def piped(encoding="cp1252", tty=False):
    """A text stream such as Windows gives a pipe or a file: in the ANSI code page (cp1252), with
    no Japanese and no U+FFFD. With `tty`, one that says it is a terminal."""
    import io

    class Bytes(io.BytesIO):
        def isatty(self):
            return tty
    return io.TextIOWrapper(Bytes(), encoding=encoding, newline="")


def run_piped(monkeypatch, argv):
    """(exit code, stdout bytes, stderr bytes) of orch `argv` with stdout and stderr such pipes."""
    from orchestraty import cli
    out, err = piped(), piped()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    code = cli.main(argv)
    out.flush()
    err.flush()
    return code, out.buffer.getvalue(), err.buffer.getvalue()
