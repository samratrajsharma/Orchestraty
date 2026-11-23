"""The command line for a hook that an agent runs through a shell (Codex and Antigravity do:
``sh`` on macOS and Linux; on Windows, PowerShell or cmd).

On macOS and Linux each argument is quoted for sh. On Windows an argument goes as it is when
neither cmd nor PowerShell needs it quoted (letters, digits and ``_ - . : \\ / ~``), so the same
line works in both; a path that needs quoting is given in its short (8.3) form when the volume
keeps one, which needs none (the file must be there first: the volume gives a short name when
the file is made). An argument after the program that still needs quoting goes in double
quotes, which PowerShell reads, and cmd too when the agent hands it the line as it is, unless it
holds ``"``, ``%``, ``$`` or a backtick (which one of them would expand). Otherwise the line is
PowerShell's (Codex's shell there): the call operator and single quotes.
"""
import os
import re
import shlex

_PLAIN = re.compile(r"[A-Za-z0-9_\-.:\\/~]+")
_EXPANDS = re.compile(r'["%$`]|\\$')         # changed inside double quotes by cmd or PowerShell, or a
                                             # backslash that would escape the closing quote


def short_path(path):
    """On Windows, the path's short (8.3) form, or the path when the volume keeps none."""
    if os.name != "nt":
        return path
    import ctypes
    buf = ctypes.create_unicode_buffer(32768)
    n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, len(buf))
    return buf.value if 0 < n < len(buf) else path


def long_path(path):
    """On Windows, the path with any short (8.3) names in it written out, or the path when it is
    not there (any more)."""
    if os.name != "nt" or "~" not in path:
        return path
    import ctypes
    buf = ctypes.create_unicode_buffer(32768)
    # The hook or state folder may have been removed since a backup was written.
    prefix, suffix = path, []
    while prefix:
        n = ctypes.windll.kernel32.GetLongPathNameW(prefix, buf, len(buf))
        if 0 < n < len(buf):
            return os.path.join(buf.value, *reversed(suffix))
        parent, name = os.path.split(prefix)
        if not name or parent == prefix:
            break
        suffix.append(name)
        prefix = parent
    return path


def _plain(arg):
    return bool(_PLAIN.fullmatch(arg))


def command_line(argv, windows=None, shorten=short_path):
    """The line a shell runs `argv` from."""
    windows = os.name == "nt" if windows is None else windows
    if not windows:
        return " ".join(shlex.quote(a) for a in argv)
    args = [a if _plain(a) else shorten(a) for a in argv]
    if all(_plain(a) for a in args):
        return " ".join(args)
    if _plain(args[0]) and not any(_EXPANDS.search(a) for a in args[1:]):
        return " ".join(a if _plain(a) else '"' + a + '"' for a in args)
    return "& " + " ".join(a if _plain(a) else "'" + a.replace("'", "''") + "'" for a in args)


# The line orch init writes for an agent, whatever the state folder: <python> -S -I <folder>/hooks/hook.py
# <agent> <event>, the script plain (or in its 8.3 form), in double quotes (cmd and PowerShell), or in single
# quotes: PowerShell's, which write ' as '', or sh's, which write it '"'"'.
_SHAPED = r"""\s-S\s-I\s(?:"(?P<dq>[^"]+)"|'(?P<sq>(?:'"'"'|''|[^'])+)'|(?P<plain>[^\s'"]+))\s{agent}\s[A-Za-z]+\s*$"""
_SCRIPT = re.compile(r"[\\/]hooks[\\/]hook\.py$")


def shaped_script(command, agent):
    """The hook script a command line runs, unquoted, when it has the shape orch init writes for
    `agent` (whatever the state folder); else None."""
    shaped = re.search(_SHAPED.replace("{agent}", re.escape(agent)), command) if isinstance(command, str) else None
    if not shaped:
        return None
    script = shaped.group("dq") or shaped.group("plain")
    if script is None:                       # a " in them is sh's: no Windows path has one
        quoted = shaped.group("sq")
        script = quoted.replace("'\"'\"'", "'") if '"' in quoted else quoted.replace("''", "'")
    return script if _SCRIPT.search(script) else None


def mentions(command, path):
    """Whether a command line names the file at `path`, in any form command_line writes it."""
    if not isinstance(command, str) or not path:
        return False

    def bare(text):
        return os.path.normcase(text.replace("'", "").replace('"', ""))
    line = bare(command)
    return any(bare(form) in line for form in {path, short_path(path)})
