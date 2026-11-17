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
    n = ctypes.windll.kernel32.GetLongPathNameW(path, buf, len(buf))
    return buf.value if 0 < n < len(buf) else path


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


def mentions(command, path):
    """Whether a command line names the file at `path`, in any form command_line writes it."""
    if not isinstance(command, str) or not path:
        return False

    def bare(text):
        return os.path.normcase(text.replace("'", "").replace('"', ""))
    line = bare(command)
    return any(bare(form) in line for form in {path, short_path(path)})
