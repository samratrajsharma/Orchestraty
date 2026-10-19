"""Reads the spool: every new complete line of every ``<spool>/<agent>/<file>.jsonl``.

Where it stopped in each file is kept as (file identity, offset), and the caller saves those
with the events it records, so a restarted coordinator neither loses nor repeats a line. A
line still being written (no newline yet) waits for the next pass. A file that was replaced
or truncated is read again from its start.
"""
import os

MAX_READ = 4 << 20          # bytes read from one file per pass
MAX_LINE = 1 << 20          # a "line" longer than this without a newline is skipped


def file_id(st):
    return "%d:%d" % (st.st_dev, st.st_ino)


class SpoolReader:
    def __init__(self, spool, positions=None):
        self.spool = spool
        self.positions = dict(positions or {})     # path -> (file id, offset)
        self.changed = {}                          # path -> (file id, offset) not yet saved
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
            fid = file_id(st)
            known = self.positions.get(path)
            offset = known[1] if known and known[0] == fid and known[1] <= st.st_size else 0
            fh.seek(offset)
            data = fh.read(MAX_READ)
        end = data.rfind(b"\n")
        if end < 0:
            if len(data) >= MAX_LINE:                      # no newline in a whole megabyte: skip it
                self.skipped += 1
                self._move(path, fid, offset + len(data))
            elif known is None or known[0] != fid:
                self._move(path, fid, offset)
            return []
        self._move(path, fid, offset + end + 1)
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
