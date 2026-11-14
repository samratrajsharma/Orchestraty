"""Delivery: turns findings into notes for the sessions that must hear, keeps the slots
filled, and learns which notes reached their agent.

Who hears what:

| Rule   | Note                                   | To                               | Slot      |
|--------|----------------------------------------|----------------------------------|-----------|
| O1     | none (weak: status only)               |                                  |           |
| O2     | another session changes this too       | both sessions                    | next      |
| O3, O4 | what changed, and where this file uses it | the session using it          | edit file |
|        | the same, when it is already changing  | the session using it             | next      |
|        | that file (it may never edit it again) |                                  |           |
|        | who uses what you changed (not for a   | the session that changed it      | next      |
|        | latent finding: a file nobody is changing) |                              |           |
| O5     | another session changes this route too | both sessions                    | next      |
| O6     | callers you left behind                | the session that changed it      | stop      |
| O3, O4 | the same note, when wake is on and the   | the session using it             | wake      |
|        | session using it waits for its user      |                                  |           |
| W1     | another session works in this same folder | each of the two (with hooks)    | next      |
| O7     | another session declared or claimed    | each of the two                  | next      |
|        | this too (never held back)             |                                  |           |
| together | each passes the user's check alone,  | each of the two                  | next      |
| check  | together it fails (opt-in)             |                                  |           |
| claim  | who claimed what, until when           | every other session (with hooks) | edit file |
|        |                                        | on the repository                | (folder)  |
|        | the same, when it has changed what is  | the same sessions                | next      |
|        | claimed already (not for an edit that  |                                  |           |
|        | must ask, below)                       |                                  |           |

Claims (``orch claim``) are not findings: their notes are kept under ``claim|<number>``, and a
claim that ends takes back the notes not yet taken. A claim on a function or class is noted
before an edit of the file that defines it; one on a folder, before an edit of any file in it,
from the folder's slot (``slots.area_slot``). A hard claim makes the edit wait for the user's
answer where the agent can ask (``adapters.asks``: Claude Code, a PreToolUse ``ask``); other
agents' sessions are informed, and the note says so. Steer never turns a claim note into a
deny: a slot denies only for a finding's note in it.

Plain-git sessions get no notes (they have no hooks), and nor do cloud agents' branches (their
agents work elsewhere). A note goes only to a session whose agent can take that kind at the
event its slot is for (``adapters``: Antigravity takes no edit note, so it hears the next note
instead once it changes the file; only Claude Code can be woken), and it is written in the form
that agent accepts. A session hears each finding once:
when one of its notes for a finding is taken (the edit note, the next note or the wake note),
the others count as delivered and come out of their slots, and no new one is made. The
record's ``notes`` table remembers every note made, so a finding that comes back is not told
twice. Nor is the same change told twice when it is found again under another
session: a plain-git session that took over a finished session's worktree, or two sessions
that broke the same function (one note per session, kind, rule, function and file). A note
whose finding stops holding before it was taken is withdrawn.

Slots hold the notes waiting for them when they are written: as many whole notes as fit in
MAX_SLOT_TEXT characters, the oldest first (those that make an edit ask or stop lead), with a
last line saying how many more wait; the rest wait for the slot's next write. Slots are never
overwritten (see ``slots``): when a slot that had notes written to it is gone from disk, the
hook took it, and those notes are delivered; then the next notes waiting for it are written.

The push line: for each live session with an open O6 finding, hooked or plain git (a worktree
an agent has left), ``push/<worktree key>.txt`` holds one line the git pre-push hook prints.
"""
import os
import shutil
import time

from orchestraty import adapters, claims
from orchestraty.delivery import notes as templates
from orchestraty.delivery import slots

NS = 1_000_000_000
MAX_SLOT_TEXT = 9000              # Claude Code saves longer hook text to a file instead
NO_NOTES = ("git", "cloud")       # no hooks here (plain git, a cloud agent's branch): nothing can be delivered


def _rel(state, path):
    return os.path.relpath(path, state.root).replace(os.sep, "/")


def _whole_notes(notes, limit):
    """The first notes whose texts, joined by blank lines, fit in `limit` characters; always the
    first one (no note is longer than notes.MAX_NOTE)."""
    size, kept = -2, []
    for n in notes:
        size += 2 + len(n["text"])
        if kept and size > limit:
            break
        kept.append(n)
    return kept


def _slot_text(notes):
    """(the notes one write of a slot carries, its text): as many whole notes as fit in
    MAX_SLOT_TEXT, and when the rest must wait for the slot's next write, a last line saying how
    many."""
    batch = _whole_notes(notes, MAX_SLOT_TEXT)
    if len(batch) < len(notes):
        room = MAX_SLOT_TEXT - 2 - len(templates.more_waiting(len(notes)))   # len(notes) bounds the count's digits
        batch = _whole_notes(notes, room)
        texts = [n["text"] for n in batch] + [templates.more_waiting(len(notes) - len(batch))]
        return batch, "\n\n".join(texts)[:MAX_SLOT_TEXT]
    return batch, "\n\n".join(n["text"] for n in batch)[:MAX_SLOT_TEXT]


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

        def hears(sid, kind):
            """Whether the session's agent can be handed a note of this kind."""
            return hooked(sid) and kind in adapters.hears(sessions[sid]["agent"])

        if rule == "O2":
            a, b = finding["sessions"]
            for me, other in ((a, b), (b, a)):
                if hears(me, "next") and other in sessions:
                    out.append((me, "next", slots.next_slot(self.state, me),
                                templates.for_both(finding, sessions[other])))
        elif rule in ("O3", "O4"):
            user, changer = finding["receiver"], finding["changer"]
            latent = finding["detail"].get("latent")
            if hooked(user) and changer in sessions:
                path = os.path.join(sessions[user]["worktree"], *finding["file"].split("/"))
                elsewhere = sessions[changer].get("worktree") != sessions[user].get("worktree")
                text = templates.for_user(finding, sessions[changer], elsewhere=elsewhere)
                if hears(user, "edit"):
                    out.append((user, "edit", slots.edit_slot(self.state, user, path), text))
                if not latent and hears(user, "next"):
                    # it already changed that file and may never edit it again: tell it with
                    # its next tool result too (whichever note it takes first counts)
                    out.append((user, "next", slots.next_slot(self.state, user),
                                templates.for_user(finding, sessions[changer], before_edit=False,
                                                   elsewhere=elsewhere)))
                if wake and sessions[user].get("turn") == "idle" and not latent and hears(user, "wake"):
                    out.append((user, "wake", slots.wake_slot(self.state, user), text))
            if hears(changer, "next") and user in sessions and not latent:
                told = hears(user, "edit") or ("next" if hears(user, "next") else False)
                out.append((changer, "next", slots.next_slot(self.state, changer),
                            templates.for_changer(finding, sessions[user], told=told)))
        elif rule == "O5":
            a, b = finding["sessions"]
            for me, other in ((a, b), (b, a)):
                if hears(me, "next") and other in sessions:
                    out.append((me, "next", slots.next_slot(self.state, me),
                                templates.same_route(finding, me, sessions[other])))
        elif rule == "O6":
            changer = finding["changer"]
            if hears(changer, "stop"):
                out.append((changer, "stop", slots.stop_slot(self.state, changer), templates.left_behind(finding)))
        elif rule == "O7":
            a, b = finding["sessions"]
            for me, other in ((a, b), (b, a)):
                if hears(me, "next") and other in sessions:
                    out.append((me, "next", slots.next_slot(self.state, me),
                                templates.planned_overlap(finding, me, sessions[other])))
        elif rule == "W1":
            a, b = finding["sessions"]
            if hooked(a) and hooked(b):                     # a plain-git session gets none and causes none
                for me, other in ((a, b), (b, a)):
                    if hears(me, "next"):
                        out.append((me, "next", slots.next_slot(self.state, me),
                                    templates.same_folder(sessions[other])))
        return out

    def plan_claim(self, claim, sessions, changed=None):
        """[(session id, kind, slot path, text)] for one claim that holds, to every other live
        session with hooks on its repository. `changed`: {session id: [posix path]}, the files
        each has changed: one that changed a file the claim covers hears with its next step
        too (it may never edit it again), unless its edit is to ask its user."""
        claimer = sessions.get(claim["session_id"])
        if claimer is None:
            return []
        out = []
        for sid, s in sorted(sessions.items()):
            if (sid == claimer["id"] or s["state"] != "live" or s["repo_id"] != claim["repo_id"]
                    or s["agent"] in NO_NOTES or not s["worktree"]):
                continue
            can = adapters.hears(s["agent"])
            ask = adapters.asks(s["agent"]) if claim["hard"] else None
            path = os.path.join(s["worktree"], *claims.path_of(claim).split("/"))
            if "edit" in can:
                slot = (slots.area_slot(self.state, sid, path, ask=bool(ask)) if claim["kind"] == "area"
                        else slots.edit_slot(self.state, sid, path))
                out.append((sid, "edit", slot, templates.claimed(claim, claimer, ask=ask)))
            if not ask and "next" in can and any(claims.covers(claim, f) for f in (changed or {}).get(sid, ())):
                out.append((sid, "next", slots.next_slot(self.state, sid),
                            templates.claimed(claim, claimer, before_edit=False)))
        return out

    def add_claim(self, claim, sessions, changed=None):
        """Make the notes a claim that holds calls for (not again for one already made, nor for a
        session that took one of them)."""
        made, key = 0, claims.note_key(claim["id"])
        for sid, kind, slot, text in self.plan_claim(claim, sessions, changed):
            if self._heard(key, sid):
                continue
            cause = max(claim["ts"], sessions[sid]["last_seen"])
            if self.record.add_note(key, sid, kind, _rel(self.state, slot), text, self.now_ns(), cause):
                made += 1
        return made

    def add_together(self, result, sessions):
        """The notes of a together check whose pair passes alone and fails together: one to each
        session (kept under ``together|<content state>``, so a new state can be told again)."""
        a, b = result["sessions"]
        key, made = f"together|{result['state']}", 0
        for me, other in ((a, b), (b, a)):
            s = sessions.get(me)
            if (s is None or s["state"] != "live" or s["agent"] in NO_NOTES or other not in sessions
                    or "next" not in adapters.hears(s["agent"])):
                continue
            if self.record.add_note(key, me, "next", _rel(self.state, slots.next_slot(self.state, me)),
                                    templates.together_fails(result, sessions[other]), self.now_ns(), self.now_ns()):
                made += 1
        return made

    def withdraw_claims(self, holding):
        """Take back the notes not yet taken of the claims that no longer hold (`holding`: the
        numbers of those that do)."""
        for key in sorted({n["finding_key"] for n in self.record.notes(states=("pending", "written"))}):
            number = claims.from_note_key(key)
            if number is not None and number not in holding:
                self.withdraw(key)

    def add(self, finding, sessions, trigger_ns, wake=False):
        """Make the notes a newly seen finding calls for (not again for one already made); a
        note still waiting for its agent is brought up to date with the finding."""
        made = 0
        for sid, kind, slot, text in self.plan(finding, sessions, wake):
            if self._told_already(finding, sid, kind) or self._heard(finding["key"], sid):
                continue
            if self.record.add_note(finding["key"], sid, kind, _rel(self.state, slot), text, self.now_ns(), trigger_ns):
                made += 1
            else:
                self._refresh(finding["key"], sid, kind, text)
        return made

    def _refresh(self, finding_key, sid, kind, text):
        """The finding still holds but what its note says changed while the note waits (a caller
        another session updated, a use that moved): the note takes the new text, and a slot
        already written is taken back, so the next fill writes it again."""
        for note in self.record.notes(finding_key=finding_key, session_id=sid, states=("pending", "written")):
            if note["kind"] != kind or note["text"] == text:
                continue
            if note["state"] == "written":
                if self._settle(note["slot"]):
                    continue                              # taken meanwhile: told as it was
                if not slots.withdraw(_abs(self.state, note["slot"])):
                    self._delivered(note["slot"], self.now_ns())
                    continue
                self.record.unwrite_slot(note["slot"])    # the slot's other notes wait for that write too
            self.record.set_note_text(note["id"], text)

    @staticmethod
    def _topic(key, sid):
        """What a finding is about for one receiving session, whoever made the change."""
        parts = key.split("|")
        if parts[0] in ("O3", "O4") and len(parts) == 5:
            return (parts[0], parts[1], parts[4])
        if parts[0] in ("O2", "O5") and len(parts) == 4 and sid in parts[2:]:
            return (parts[0], parts[1])
        return None

    def _heard(self, finding_key, sid):
        """Whether the session already took a note of this finding."""
        return bool(self.record.notes(finding_key=finding_key, session_id=sid, states=("delivered",)))

    def _told_already(self, finding, sid, kind):
        topic = self._topic(finding["key"], sid)
        if topic is None:
            return False
        for note in self.record.notes(session_id=sid, states=("pending", "written", "delivered")):
            if note["kind"] == kind and note["finding_key"] != finding["key"] and self._topic(note["finding_key"], sid) == topic:
                return True
        return False

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
            self.record.set_note_state(note["id"], "withdrawn", self.now_ns())

    # 2. notes -> slots
    def _settle(self, slot_rel):
        """If a slot with written notes is gone from disk, the hook took it: delivered."""
        if not slots.waiting(_abs(self.state, slot_rel)) and self.record.notes(slot=slot_rel, states=("written",)):
            self._delivered(slot_rel, self.now_ns())
            return True
        return False

    def _delivered(self, slot_rel, ts):
        """The slot's written notes reached their agent. A session hears each finding once:
        that finding's other notes for the same session count as delivered too, and come out
        of their slots (the other notes there wait for the next write)."""
        taken = self.record.notes(slot=slot_rel, states=("written",))
        self.record.mark_delivered(slot_rel, ts)
        for note in taken:
            for other in self.record.notes(finding_key=note["finding_key"], session_id=note["session_id"],
                                           states=("pending", "written")):
                if other["state"] == "written" and slots.withdraw(_abs(self.state, other["slot"])):
                    self.record.unwrite_slot(other["slot"])
                self.record.set_note_state(other["id"], "delivered", ts)

    def forget_session(self, session_id):
        """A session ended: take back its waiting notes and remove its folder."""
        for note in self.record.notes(session_id=session_id, states=("pending", "written")):
            if note["state"] == "written":
                slots.withdraw(_abs(self.state, note["slot"]))
            self.record.set_note_state(note["id"], "withdrawn", self.now_ns())
        shutil.rmtree(slots.folder(self.state, session_id), ignore_errors=True)

    def fill(self, steer=False):
        """Write every free slot that has notes waiting, in the form its session's agent
        accepts: as many whole notes as fit in MAX_SLOT_TEXT; the rest wait for the slot's next
        write. Returns the slots written."""
        written, agents = [], {}
        for slot_rel in self.record.slots_with(("pending", "written")):
            self._settle(slot_rel)
            pending = self.record.notes(slot=slot_rel, states=("pending",))
            if not pending or self.record.notes(slot=slot_rel, states=("written",)):
                continue                                  # nothing new, or the slot still waits
            sid, kind = pending[0]["session_id"], pending[0]["kind"]
            if sid not in agents:
                row = self.record.session(sid)
                agents[sid] = row["agent"] if row else ""
            asks, stops = self._deciders(pending, steer)       # over every note waiting, not only those written now
            lead = asks | stops
            pending.sort(key=lambda n: n["id"] not in lead)     # those that make the edit ask or stop lead, so the text says why
            batch, text = _slot_text(pending)
            data = adapters.output(agents[sid], kind, text, steer=bool(stops), ask=bool(asks))
            if slots.write(_abs(self.state, slot_rel), data):
                self.record.mark_written([n["id"] for n in batch], self.now_ns())
                written.append(slot_rel)
        return written

    def _deciders(self, notes, steer):
        """Ids of the notes that make an edit wait for the user's answer (a hard claim's) and, with
        steer, of those that stop it (a strong finding's: never a claim's, nor a medium one's, such
        as an O3 about TypeScript, whose uses are heuristic)."""
        asks, stops = set(), set()
        for n in notes:
            number = claims.from_note_key(n["finding_key"])
            if number is not None:
                c = self.record.claim(number)
                if c and c["hard"]:
                    asks.add(n["id"])
            elif steer and (self.record.finding(n["finding_key"]) or {}).get("strength") == "strong":
                stops.add(n["id"])
        return asks, stops

    def taken(self, slot_rel, ts):
        """A hook's spool line says it took this slot."""
        if self.record.notes(slot=slot_rel, states=("written",)):
            self._delivered(slot_rel, ts)

    # 3. the push line
    def push_lines(self, sessions, open_findings):
        """Write or remove push/<worktree key>.txt for every live session, hooked or plain git."""
        folder = os.path.join(self.state.root, "push")
        wanted = {}
        for s in sessions.values():
            if s["state"] != "live" or not s["worktree"]:
                continue                                  # a cloud branch has no worktree to push from
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
