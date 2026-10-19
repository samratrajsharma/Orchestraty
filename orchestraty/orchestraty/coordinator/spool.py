"""Reads the spool: every new complete line of every ``<spool>/<agent>/<file>.jsonl``.

Where it stopped in each file is kept as (file identity, offset), and the caller saves those
with the events it records, so a restarted coordinator neither loses nor repeats a line. A
line still being written (no newline yet) waits for the next pass. A file that was replaced
or truncated is read again from its start.

The identity is the file's device and inode numbers plus the last bytes read before the
offset: a file deleted and created again can get the same inode at once (Linux reuses them),
but not the same bytes there, since every line carries a nanosecond timestamp.
"""
import os

MAX_READ = 4 << 20          # bytes read from one file per pass
MAX_LINE = 1 << 20          # a "line" longer than this without a newline is skipped
TAIL = 16                   # bytes before the offset kept to recognise the file


def _identity(st, tail):
    return "%d:%d:%s" % (st.st_dev, st.st_ino, tail.hex())


def _verified_tail(known_id, st, fh, offset):
    """The bytes before `offset` if the open file is the one `known_id` describes, else None."""
    parts = known_id.split(":")
    if len(parts) != 3 or parts[:2] != [str(st.st_dev), str(st.st_ino)] or offset > st.st_size:
        return None
    try:
        tail = bytes.fromhex(parts[2])
    except ValueError:
        return None
    start = max(0, offset - len(tail))
    fh.seek(start)
    return tail if fh.read(offset - start) == tail else None


class SpoolReader:
    def __init__(self, spool, positions=None):
        self.spool = spool
        self.positions = dict(positions or {})     # path -> (file identity, offset)
        self.changed = {}                          # path -> (file identity, offset) not yet saved
        self.skipped = 0                           # overlong partial lines dropped

    def _files(self):
        try:
            agents = [e for e in os.scandir(self.spool) if e.is_dir(follow_symlinks=False)]
        except OSError:
            return
        for agent in agents:
            try:
                entries = list(os.scandir(agent.path))
            except OSError:
                continue
            for entry in entries:
                if entry.name.endswith(".jsonl") and entry.is_file(follow_symlinks=False):
                    yield agent.name, entry

    def poll(self):
        """[(agent, path, line bytes)] for the complete lines written since the last poll."""
        out = []
        for agent, entry in self._files():
            path = entry.path
            known = self.positions.get(path)
            try:
                # os.stat, not the directory listing's copy: on Windows the listing's size can
                # lag behind a file another process just appended to.
                size = os.stat(path, follow_symlinks=False).st_size
            except OSError:
                continue
            if known is not None and size == known[1]:
                continue                                   # nothing new (the usual case)
            out.extend((agent, path, line) for line in self._read(path))
        return out

    def _read(self, path):
        try:
            fh = open(path, "rb")
        except OSError:
            return []
        with fh:
            st = os.fstat(fh.fileno())
            known = self.positions.get(path)
            before = _verified_tail(known[0], st, fh, known[1]) if known else None
            offset = known[1] if before is not None else 0
            before = before or b""
            fh.seek(offset)
            data = fh.read(MAX_READ)
        end = data.rfind(b"\n")
        if end < 0:
            if len(data) >= MAX_LINE:                      # no newline in a whole megabyte: skip it
                self.skipped += 1
                self._move(path, _identity(st, (before + data)[-TAIL:]), offset + len(data))
            elif known is None or offset != known[1]:
                self._move(path, _identity(st, before), offset)
            return []
        self._move(path, _identity(st, (before + data[:end + 1])[-TAIL:]), offset + end + 1)
        return [line for line in data[:end].split(b"\n") if line.strip()]

    def _move(self, path, fid, offset):
        self.positions[path] = (fid, offset)
        self.changed[path] = (fid, offset)

    def take_changes(self):
        """The positions moved since the last call, for the caller to save."""
        changed, self.changed = self.changed, {}
        return changed

    def forget(self, path):
        self.positions.pop(path, None)
        self.changed.pop(path, None)
