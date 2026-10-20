"""Delivery: turns findings into notes for the sessions that must hear, keeps the slots
filled, and learns which notes reached their agent.

Who hears what (Draft 4 §5.5):

| Rule   | Note                                   | To                               | Slot      |
|--------|----------------------------------------|----------------------------------|-----------|
| O1     | none (weak: status only)               |                                  |           |
| O2     | another session changes this too       | both sessions                    | next      |
| O3, O4 | what changed, and where this file uses it | the session using it          | edit file |
|        | who uses what you changed              | the session that changed it      | next      |
| O6     | callers you left behind                | the session that changed it      | stop      |
| O3, O4 | the same note, when wake is on and the   | the session using it             | wake      |
|        | session using it waits for its user      |                                  |           |

Plain-git sessions get no notes (they have no hooks). One note per finding per session and
kind: the record's ``notes`` table remembers every note made, so a finding that comes back
is not told twice. A note whose finding stops holding before it was taken is withdrawn.

Slots hold every note waiting for them when they are written and are never overwritten (see
``slots``): when a slot that had notes written to it is gone from disk, the hook took it, and
those notes are delivered; then the next notes waiting for it are written.

The push line: for each live hooked session with an open O6 finding, ``push/<worktree key>.txt``
holds one line the git pre-push hook prints.
"""
import os
import shutil
import time

from orchestraty.delivery import notes as templates
from orchestraty.delivery import slots

NS = 1_000_000_000
MAX_SLOT_TEXT = 9000              # Claude Code saves longer hook text to a file instead
NO_NOTES = ("git",)               # agents without hooks: nothing can be delivered to them


def _rel(state, path):
    return os.path.relpath(path, state.root).replace(os.sep, "/")


def _abs(state, rel):
    return os.path.join(state.root, *rel.split("/"))


class Deliverer:
    def __init__(self, state, record, log, now_ns=time.time_ns):
        self.state = state
        self.record = record
        self.log = log
        self.now_ns = now_ns

    # 1. findings -> notes
    def plan(self, finding, sessions, wake=False):
        """[(session id, kind, slot path, text)] for one finding (a dict as the record keeps it)."""
        rule = finding["rule"]
        out = []

        def hooked(sid):
            s = sessions.get(sid)
            return s is not None and s["state"] == "live" and s["agent"] not in NO_NOTES

        if rule == "O2":
            a, b = finding["sessions"]
            for me, other in ((a, b), (b, a)):
                if hooked(me) and other in sessions:
                    out.append((me, "next", slots.next_slot(self.state, me),
                                templates.for_both(finding, sessions[other])))
        elif rule in ("O3", "O4"):
            user, changer = finding["receiver"], finding["changer"]
            if hooked(user) and changer in sessions:
                path = os.path.join(sessions[user]["worktree"], *finding["file"].split("/"))
                text = templates.for_user(finding, sessions[changer])
                out.append((user, "edit", slots.edit_slot(self.state, user, path), text))
                if wake and sessions[user].get("turn") == "idle":
                    out.append((user, "wake", slots.wake_slot(self.state, user), text))
            if hooked(changer) and user in sessions:
                out.append((changer, "next", slots.next_slot(self.state, changer),
                            templates.for_changer(finding, sessions[user])))
        elif rule == "O6":
            changer = finding["changer"]
            if hooked(changer):
                out.append((changer, "stop", slots.stop_slot(self.state, changer), templates.left_behind(finding)))
        return out

    def add(self, finding, sessions, trigger_ns, wake=False):
        """Make the notes a newly seen finding calls for (not again for one already made)."""
        made = 0
        for sid, kind, slot, text in self.plan(finding, sessions, wake):
            if self.record.add_note(finding["key"], sid, kind, _rel(self.state, slot), text, self.now_ns(), trigger_ns):
                made += 1
        return made

    def withdraw(self, finding_key):
        """Take back the notes of a finding that stopped holding, unless already taken."""
        for note in self.record.notes(finding_key=finding_key, states=("pending", "written")):
            if note["state"] == "written":
                self._settle(note["slot"])            # was it taken meanwhile?
                note = self.record.note(note["id"])
                if note["state"] != "written":
                    continue
                if not slots.withdraw(_abs(self.state, note["slot"])):
                    self._delivered(note["slot"], self.now_ns())
                    continue
                # the slot may have held other notes too: they wait for the next write
                self.record.unwrite_slot(note["slot"])
            self.record.set_note_state(note["id"], "withdrawn")

    # 2. notes -> slots
    def _settle(self, slot_rel):
        """If a slot with written notes is gone from disk, the hook took it: delivered."""
        if not slots.waiting(_abs(self.state, slot_rel)) and self.record.notes(slot=slot_rel, states=("written",)):
            self._delivered(slot_rel, self.now_ns())
            return True
        return False

    def _delivered(self, slot_rel, ts):
        """The slot's written notes reached their agent. A wake note carries the same text as
        its finding's other notes for that session: those count as delivered too."""
        woke = [n for n in self.record.notes(slot=slot_rel, states=("written",)) if n["kind"] == "wake"]
        self.record.mark_delivered(slot_rel, ts)
        for note in woke:
            for other in self.record.notes(finding_key=note["finding_key"], session_id=note["session_id"],
                                           states=("pending", "written")):
                if other["state"] == "written" and slots.withdraw(_abs(self.state, other["slot"])):
                    self.record.unwrite_slot(other["slot"])
                self.record.set_note_state(other["id"], "delivered")

    def forget_session(self, session_id):
        """A session ended: take back its waiting notes and remove its folder."""
        for note in self.record.notes(session_id=session_id, states=("pending", "written")):
            if note["state"] == "written":
                slots.withdraw(_abs(self.state, note["slot"]))
            self.record.set_note_state(note["id"], "withdrawn")
        shutil.rmtree(slots.folder(self.state, session_id), ignore_errors=True)

    def fill(self, steer=False):
        """Write every free slot that has notes waiting. Returns the slots written."""
        written = []
        for slot_rel in self.record.slots_with(("pending", "written")):
            self._settle(slot_rel)
            pending = self.record.notes(slot=slot_rel, states=("pending",))
            if not pending or self.record.notes(slot=slot_rel, states=("written",)):
                continue                                  # nothing new, or the slot still waits
            text = "\n\n".join(n["text"] for n in pending)[:MAX_SLOT_TEXT]
            kind = pending[0]["kind"]
            data = (templates.pre_tool_output(text, steer) if kind == "edit" else
                    templates.post_tool_output(text) if kind == "next" else
                    templates.stop_output(text) if kind == "stop" else text.encode("utf-8"))
            if slots.write(_abs(self.state, slot_rel), data):
                self.record.mark_written([n["id"] for n in pending], self.now_ns())
                written.append(slot_rel)
        return written

    def taken(self, slot_rel, ts):
        """A hook's spool line says it took this slot."""
        if self.record.notes(slot=slot_rel, states=("written",)):
            self._delivered(slot_rel, ts)

    # 3. the push line
    def push_lines(self, sessions, open_findings):
        """Write or remove push/<worktree key>.txt for every live hooked session."""
        folder = os.path.join(self.state.root, "push")
        wanted = {}
        for s in sessions.values():
            if s["state"] != "live" or s["agent"] in NO_NOTES:
                continue
            mine = [f for f in open_findings if f["rule"] == "O6" and f["changer"] == s["id"]]
            line = templates.push_line(mine)
            if line:
                wanted[slots.file_key(s["worktree"]) + ".txt"] = line
        os.makedirs(folder, exist_ok=True)
        for name in os.listdir(folder):
            if name.endswith(".txt") and name not in wanted:
                try:
                    os.remove(os.path.join(folder, name))
                except OSError:
                    pass
        for name, line in wanted.items():
            path = os.path.join(folder, name)
            data = (line + "\n").encode("utf-8")
            try:
                with open(path, "rb") as fh:
                    if fh.read() == data:
                        continue
            except OSError:
                pass
            tmp = path + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
