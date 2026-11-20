"""What a person says a session of theirs works on, for teammates: a ticket and a task line.

``orch team task`` appends a ``team-task`` line to orch's own spool, and the coordinator keeps the
latest word for each session in the record (``team_confirmed``). A line is shared only under the
policy's ``share.task: confirmed``, and only once its author wrote or confirmed it: an agent's
declared task is never shared on its own. It belongs to one session and ends with it.
"""
import re
import time

from orchestraty import feedback
from orchestraty.team import grammar

EVENT = "team-task"
MAX_SESSION = 200
_TICKET = re.compile(r"[A-Z][A-Z0-9]{1,9}-[0-9]{1,7}|(?:#|GH-)[0-9]{1,7}")


def is_ticket(value):
    """A ticket as orch team task takes it: KEY-123, #123 or GH-123 (the policy's keys are checked
    when a record is sealed)."""
    return isinstance(value, str) and bool(_TICKET.fullmatch(value))


def append(state, session_id, ticket=None, line=None, clear=False, now=None):
    """Append a session's confirmed ticket or line (or that both are cleared) for the coordinator."""
    fields = {"session_id": session_id}
    if clear:
        fields["clear"] = True
    if ticket is not None:
        fields["ticket"] = ticket
    if line is not None:
        fields["line"] = line
    feedback.append_line(state, EVENT, time.time() if now is None else now, **fields)


def parse_line(line):
    """{"session_id", "clear", and "ticket" and "line" when given} from one spool line, checked, or
    None for a line of any other shape."""
    if line.get("event") != EVENT:
        return None
    sid = line.get("session_id")
    if not isinstance(sid, str) or not 0 < len(sid) <= MAX_SESSION:
        return None
    item = {"session_id": sid, "clear": line.get("clear") is True}
    if "ticket" in line:
        if not (line["ticket"] == "" or is_ticket(line["ticket"])):
            return None
        item["ticket"] = line["ticket"]
    if "line" in line:
        if not (line["line"] == "" or grammar.task_line(line["line"])):
            return None
        item["line"] = line["line"]
    return item
