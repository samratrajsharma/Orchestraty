"""``orch team``: team sync for one watched repository, from the command line.

``orch team on`` reads the remote's default branch (asking first, since it is a fetch), checks
the policy and the people file in force there, asks the anonymous-read probe, shows what will be
shared, where and when, and asks again; then it makes the device and keeps the policy agreed to.
``off`` stops it, and ``--forget`` deletes this device's record ref on the remote. ``init`` prints a
policy and a people file to start from, ``check`` reads them in a working copy, ``whoami`` says who
this machine shares as, ``preview`` shows the exact bytes a record would have now, ``task`` gives a
session a ticket or a task line its author confirms, and ``device`` labels or renews the device.

In this version nothing is pushed and no teammate's record is read: the record is only shown,
by ``orch team preview``.
"""
import json
import os
import re
import sys
import time

from orchestraty import __version__, config, gitio, remote, team
from orchestraty.coordinator.record import Record
from orchestraty.delivery.notes import printable
from orchestraty.team import (grammar, identity, people, policy, probe, pushwatch, recordfile, scan, seal, snapshot,
                              store, tasks, teamfile)

PEOPLE_FILE = ".orchestraty/people.yml"
CODEOWNERS_LINE = "/.orchestraty/ @your-org/leads"
OUTBOX = store.OUTBOX
POLICY_TEMPLATE = """\
# What Orchestraty shares between the teammates of this repository. The copy in force is the one
# on the remote's default branch: a change made on another branch counts once it is merged.
version: 1
sync: on                  # on or off
visibility: private       # private or public: public (with public_sync: true) if anyone can read it
public_sync: false
share:
  claims: true            # live claims: their kind, target and end (for teammates, they only inform)
  task: ticket            # none, ticket or confirmed: confirmed also shares lines their authors confirm
  unpushed: false         # committed but unpushed work: its files and functions
  activity: false         # whether a session's agent is working or idle
tickets:
  keys: []                # ticket keys, such as [PAY, OPS] for PAY-123 and OPS-7
  issues: false           # #123 and GH-123
branches:
  share: ["*"]            # branch globs, one * at most
  never: []
deny:
  paths: []               # path globs never named in a record; a built-in list of secret files is added
  words: []               # words never shared, in any case
people:
  unlisted: share         # share or refuse: whether someone not in people.yml shares, as unlisted-<hash>
signatures: off
"""
_NOT_HANDLE = re.compile(r"[^a-z0-9]+")


class Refused(Exception):
    """What was asked cannot be done; the message says why and, where there is one, the fix."""


def run(args, confirm):
    """orch team and its actions; `confirm(question, assume_yes)` asks the person."""
    from orchestraty.paths import State
    state = State()
    action = ACTIONS[args.action or "show"]
    try:
        return action(state, args, confirm)
    except Refused as exc:
        print(f"orch team: {exc}", file=sys.stderr)
        return 2


# ------------------------------------------------------------------------------- where

def _worktree(args):
    path = os.path.abspath(getattr(args, "repo", None) or os.getcwd())
    try:
        info = gitio.worktree_info(path)
    except gitio.UntrustedRepositoryError as exc:
        raise Refused(str(exc)) from None
    if info is None:
        raise Refused(f"{path} is not inside a git worktree")
    return info


def _watched(state, args):
    info = _worktree(args)
    if not config.is_watched(config.load(state), info["common_dir"]):
        raise Refused(f"{os.path.basename(info['top'])} is not watched: run orch watch in it first")
    return info


def _default_ref(name):
    return f"{remote.REF_ROOT}/{name}/HEAD"


def _people_in_force(shadow, name):
    """The people file on the default branch as last fetched, People.empty() when it has none,
    or None when the default branch was never fetched. Raises people.Error."""
    if not shadow.exists() or shadow.git(["rev-parse", "--verify", "--quiet", _default_ref(name)])[0] != 0:
        return None
    try:
        data = shadow.read_file(_default_ref(name), PEOPLE_FILE, people.MAX_BYTES)
    except remote.TooLarge:
        raise people.Error(f"the file is larger than {people.MAX_BYTES // 1024} KiB") from None
    return people.People.empty() if data is None else people.People.parse(data)


def effective_for(state, common_dir, cfg=None):
    """(settings, shadow, policy in force, agreed copy, effective policy) for one repository, as
    last fetched: no network."""
    cfg = cfg or config.load(state)
    chosen = team.settings(cfg, common_dir)
    shadow = remote.Shadow(state, common_dir)
    try:
        force = policy.in_force(shadow, chosen["remote"])
    except (gitio.GitError, remote.ShadowBroken):
        force = None
    agreed = policy.from_normalised(chosen["consented"]) if chosen["consented"] else None
    answer, _ = probe.last(shadow)
    eff = policy.effective(force, agreed, chosen["narrow"], chosen["never_branches"], anonymous_read=answer)
    return chosen, shadow, force, agreed, eff


def suggested_handle(email):
    """A handle for people.yml made from an email's name: lowercase words joined by dashes."""
    name = email.split("@", 1)[0]
    if email.lower().endswith(people.NOREPLY) and "+" in name:
        name = name.split("+", 1)[1]                       # 1234+alice@users.noreply.github.com
    handle = _NOT_HANDLE.sub("-", name.lower()).strip("-")[:39].strip("-")
    return handle if grammar.handle(handle) else "me"


def people_entry(handle, email):
    return f"  - handle: {handle}\n    emails: [{email}]\n"


def _share_words(eff):
    """(what is shared, what is not), in a few words each."""
    yes, no = ["sessions", "branches"], []
    (yes if eff.claims else no).append("claims")
    (yes if eff.task != "none" else no).append("tickets")
    (yes if eff.task == "confirmed" else no).append("task lines")
    (yes if eff.unpushed else no).append("unpushed work")
    (yes if eff.activity else no).append("activity")
    return yes, no


# ------------------------------------------------------------------------------- the actions

def show(state, args, confirm):
    info = _watched(state, args)
    name = os.path.basename(info["top"])
    chosen, _, force, agreed, eff = effective_for(state, info["common_dir"])
    print(f"Team sync of {name}: {'on' if chosen['on'] else 'off'}. Remote: {chosen['remote']}.")
    if force is None:
        print(f"No team policy is known for {chosen['remote']}'s default branch (orch team on reads it).")
        return 0
    off = eff.off()
    if off:
        print(f"Sharing: none, since {off[0]}; fix: {off[1]}.")
    else:
        yes, no = _share_words(eff)
        print(f"Sharing: {', '.join(yes)}" + (f" (not: {', '.join(no)})" if no else "") + ".")
    if agreed is not None and policy.widened(force, agreed):
        print("The policy in force allows more than you agreed to: orch team on to review it.")
    return 0


def on(state, args, confirm):
    info = _watched(state, args)
    common_dir, name = info["common_dir"], os.path.basename(info["top"])
    chosen = team.settings(config.load(state), common_dir)
    remote_name = args.remote or chosen["remote"]
    try:
        url = remote.remote_url(common_dir, remote_name)
        email = identity.my_email(common_dir)
    except (remote.RemoteError, identity.NoEmail) as exc:
        raise Refused(str(exc)) from None
    shadow = remote.Shadow(state, common_dir)
    asked = probe.https_form(url)
    print(f"orch team on first reads {remote_name}'s default branch ({remote.safe_url(url)}) into Orchestraty's own "
          f"repository, {shadow.path}: a read-only fetch, for the team policy and the people file in force there.")
    if asked:
        print(f"It also asks {asked}, with no credentials, whether anyone can read the repository without signing in.")
    if not confirm("Read them now?", args.yes):
        print("Nothing changed.")
        return 1
    try:
        force = policy.in_force(shadow, remote_name, url=url)
    except remote.RemoteError as exc:
        raise Refused(f"could not read {remote_name}'s default branch: {exc}") from None
    if force is None:
        raise Refused(f"there is no team policy on {remote_name}'s default branch: orch team init prints one to "
                      "start from")
    if force.off():
        raise Refused("team sync stays off: {}; fix: {}".format(*force.off()))
    try:
        folks = _people_in_force(shadow, remote_name) or people.People.empty()
    except people.Error as exc:
        raise Refused(f"the people file in force cannot be read ({exc}): fix {PEOPLE_FILE}") from None
    me = identity.who_am_i(folks, email)
    if not me.listed and force.unlisted != "share":
        raise Refused(f"you are not in {PEOPLE_FILE}, and the policy shares only for the people it lists: "
                      "orch team whoami prints the lines to add")
    answer = probe.check(shadow, url, time.time()) if asked else None
    candidate = policy.effective(force, force, chosen["narrow"], chosen["never_branches"], anonymous_read=answer)
    if candidate.off():
        raise Refused("team sync stays off: {}; fix: {}".format(*candidate.off()))
    agreed = policy.from_normalised(chosen["consented"]) if chosen["consented"] else None
    widened = policy.widened(force, agreed) if agreed is not None else []
    if chosen["on"] and agreed is not None and not widened:
        print(f"Team sync is on for {name} already, and the policy in force asks for nothing new.")
        return 0
    _describe(state, remote_name, url, candidate, me, widened, agreed is not None, chosen["on"])
    if not confirm("Share this with your teammates?", args.yes):
        print("Nothing changed.")
        return 1
    device = identity.Device.load_or_create(state)
    team.save_settings(state, common_dir, on=True, remote=remote_name, consented=force.normalised())
    pushwatch.PushWatch(shadow, common_dir, remote_name).check()        # from here on, a push sends the record
    print(f"Team sync on for {name}: you are {me.person} on device {device.id}. In this version nothing is pushed "
          "yet; orch team preview shows the record as it would be.")
    return 0


def _describe(state, remote_name, url, eff, me, widened, agreed_before, already_on):
    try:
        device = identity.Device.load(state)
    except identity.DeviceError:
        device = None
    yes, no = _share_words(eff)
    print(f"Team sync will share, with everyone who can read {remote_name} ({remote.safe_url(url)}):")
    print(f"  - who you are: {me.person} ("
          + ("in .orchestraty/people.yml" if me.listed else "not in .orchestraty/people.yml, so as an unlisted teammate")
          + "), and this device")
    print("  - for each session here on a branch the policy shares (" + ", ".join(eff.share_branches)
          + (f"; never {', '.join(eff.never_branches)}" if eff.never_branches else "") + "): its agent kind, "
          "state and times, its branch, its base and the commit the remote has")
    if eff.claims:
        print("  - its live claims: what it reserved, and until when")
    if eff.task != "none":
        keys = ", ".join(eff.ticket_keys) or "no keys yet"
        print(f"  - the ticket in its branch's name ({keys}" + ("; #123 and GH-123" if eff.ticket_issues else "") + ")")
    if eff.task == "confirmed":
        print("  - the task line you confirm with orch team task")
    if eff.unpushed:
        print("  - the files and functions of its committed but unpushed work")
    if eff.activity:
        print("  - whether its agent is working or idle")
    print("  Not: " + ", ".join(no + ["prompts", "transcripts", "file contents", "paths outside the repository",
                                      "your email", "host names", "commit messages"]) + ".")
    ref = f"refs/orchestraty/{me.person}/{device.id if device else '<this device>'}"
    print(f"It goes in one ref of its own, {ref}, pushed right after your own successful push to {remote_name}, and "
          f"teammates' records are read from {remote_name} every few minutes.")
    if eff.visibility == "public":
        print("The policy says the repository is public: anyone who can read it can read what you share.")
    if widened:
        print("The policy in force allows more than you agreed to before:")
        for line in widened:
            print(f"    - {line}")
    elif agreed_before and not already_on:
        print("This is the policy you agreed to before.")
    print("orch team preview shows the exact bytes before anything is sent.")


def off(state, args, confirm):
    info = _watched(state, args)
    common_dir, name = info["common_dir"], os.path.basename(info["top"])
    chosen = team.settings(config.load(state), common_dir)
    if args.forget:
        if not confirm(f"Delete your record ref on {chosen['remote']} (made from this device)?", args.yes):
            print("Nothing changed.")
            return 1
        _forget(state, common_dir, chosen["remote"])
    team.save_settings(state, common_dir, on=False)
    print(f"Team sync off for {name}: nothing more is sent or read.")
    return 0


def _forget(state, common_dir, remote_name):
    """Delete this device's record ref on the remote, with a lease on what this device pushed."""
    try:
        device = identity.Device.load(state)
    except identity.DeviceError:
        device = None
    shadow = remote.Shadow(state, common_dir)
    outbox = OUTBOX.format(remote_name)
    rc, tip = shadow.git(["rev-parse", "--verify", "--quiet", outbox]) if device and shadow.exists() else (1, "")
    if rc != 0:
        print("No record of yours was pushed from this device, so there is nothing to delete.")
        return
    tip = tip.strip()
    rc, data = shadow.git(["cat-file", "blob", f"{tip}:record.json"], raw=True)
    try:
        person = recordfile.parse_structure(data).record.person if rc == 0 else ""
    except recordfile.Refused:
        person = ""
    if not grammar.person(person):
        raise Refused("this device's last record cannot be read, so its ref is not deleted")
    try:
        url = remote.remote_url(common_dir, remote_name)
    except remote.RemoteError as exc:
        raise Refused(str(exc)) from None
    dst = f"refs/orchestraty/{person}/{device.id}"
    result = shadow.push(url, "", dst, tip)
    if result.kind not in ("deleted", "up to date"):
        raise Refused(f"{dst} was not deleted on {remote_name}: {result.kind} ({result.detail})")
    shadow.git(["update-ref", "-d", outbox])
    print(f"Deleted {dst} on {remote_name}.")


def whoami(state, args, confirm):
    info = _worktree(args)
    try:
        email = identity.my_email(info["common_dir"])
    except identity.NoEmail as exc:
        raise Refused(str(exc)) from None
    chosen = team.settings(config.load(state), info["common_dir"])
    shadow = remote.Shadow(state, info["common_dir"])
    where = f"as in force on {chosen['remote']}'s default branch"
    try:
        folks = _people_in_force(shadow, chosen["remote"])
    except people.Error as exc:
        print(f"The people file in force cannot be read ({exc}).")
        folks = people.People.empty()
    if folks is None:
        where, path = "as in this working copy", os.path.join(info["top"], *PEOPLE_FILE.split("/"))
        try:
            with open(path, "rb") as fh:
                folks = people.People.parse(fh.read())
        except FileNotFoundError:
            folks, where = people.People.empty(), "with no people file in this working copy"
        except people.Error as exc:
            print(f"{PEOPLE_FILE} in this working copy cannot be read ({exc}).")
            folks = people.People.empty()
    me = identity.who_am_i(folks, email)
    print(f"email: {email}")
    if me.listed:
        print(f"you are: {me.person} (in {PEOPLE_FILE}, {where})")
    else:
        print(f"you are: {me.person} (not in {PEOPLE_FILE}, {where}: you share as an unlisted teammate)")
    try:
        device = identity.Device.load(state)
    except identity.DeviceError as exc:
        raise Refused(str(exc)) from None
    print(f"device: {device.id}" + (f" ({device.label})" if device.label else "") if device else
          "device: none yet (orch team on makes one)")
    if not me.listed:
        print(f"To be listed, add these lines to {PEOPLE_FILE} and merge them into the default branch:")
        print(people_entry(suggested_handle(email), email), end="")
    return 0


def init(state, args, confirm):
    info = _worktree(args)
    try:
        email = identity.my_email(info["common_dir"])
        handle = suggested_handle(email)
    except identity.NoEmail:
        email, handle = "you@example.com", "you"
        print(f"(no git email is set for this repository, so the people file names {email}: "
              f"{identity.FIX_EMAIL} sets yours)")
    files = {".orchestraty/policy.yml": POLICY_TEMPLATE, PEOPLE_FILE: "version: 1\npeople:\n" + people_entry(handle, email)}
    for path, text in files.items():
        print(f"# {path}")
        print(text, end="")
    print("# CODEOWNERS: so that a change to the policy or the people file is reviewed")
    print(CODEOWNERS_LINE)
    if not args.write:
        print("orch team init --write writes the two files (it asks first, and commits nothing).")
        return 0
    if not confirm(f"Write the two files in {info['top']}?", False):
        print("Nothing written.")
        return 1
    for path, text in files.items():
        target = os.path.join(info["top"], *path.split("/"))
        if os.path.exists(target):
            print(f"{path} is there already: kept as it is.")
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"Wrote {path}.")
    print("Nothing is committed: check them with orch team check, then commit them and merge them into the default "
          "branch, where the copy in force is read.")
    return 0


def check(state, args, confirm):
    folder = os.path.abspath(args.path) if args.path else _worktree(args)["top"]
    if os.path.isdir(os.path.join(folder, ".orchestraty")):
        folder = os.path.join(folder, ".orchestraty")
    problems, found = 0, False
    path = os.path.join(folder, "policy.yml")
    if os.path.isfile(path):
        found = True
        with open(path, "rb") as fh:
            pol = policy.parse(fh.read())
        off = pol.off()
        if pol.problem or (pol.sync and off):
            problems += 1
            print(f"policy.yml: {off[0]}; fix: {off[1]}")
        else:
            print("policy.yml: ok" + ("" if pol.sync else " (it says sync: off)"))
        for key in pol.ignored:
            print(f"policy.yml: {key} is not a setting this version knows, so it grants nothing")
        for text in pol.notes:
            print(f"policy.yml: note: {text}")
    path = os.path.join(folder, "people.yml")
    if os.path.isfile(path):
        found = True
        with open(path, "rb") as fh:
            data = fh.read()
        try:
            listed = people.People.parse(data)
        except people.Error as exc:
            problems += 1
            print(f"people.yml: {exc}")
        else:
            print(f"people.yml: ok, {len(listed)} {'person' if len(listed) == 1 else 'people'}")
    if not found:
        print(f"No policy.yml or people.yml in {folder}.")
        return 1
    return 1 if problems else 0


def preview(state, args, confirm):
    info = _watched(state, args)
    sealed = sealed_now(state, info["common_dir"], time.time())
    withheld = [{"field": w.field, "rule": w.rule} for w in sealed.withheld]
    if args.json:
        data = sealed.data.decode("ascii") if sealed.data else None
        print(json.dumps({"schema": 1, "bytes": data, "record": json.loads(data) if data else None,
                          "withheld": withheld, "held": sealed.held}, indent=2))
        return 0
    if sealed.data is None:
        print(f"The record is held back ({sealed.held}): nothing would be sent.")
    else:
        print(f"The record this machine would send now ({len(sealed.data)} bytes, exactly as below):")
        sys.stdout.write(sealed.data.decode("ascii"))
    for w in withheld:
        print(f"withheld: {w['field']} ({w['rule']})")
    if not withheld:
        print("Nothing was withheld.")
    return 0


def sealed_now(state, common_dir, now):
    """seal.Sealed for the record this machine would send for a repository now. Raises Refused
    while team sync is off for it."""
    cfg = config.load(state)
    chosen, shadow, force, agreed, eff = effective_for(state, common_dir, cfg)
    if not chosen["on"]:
        raise Refused("team sync is off for this repository: orch team on first")
    off = eff.off()
    if off:
        raise Refused(f"team sync is off: {off[0]}; fix: {off[1]}")
    try:
        email = identity.my_email(common_dir)
        device = identity.Device.load(state)
        folks = _people_in_force(shadow, chosen["remote"]) or people.People.empty()
    except (identity.NoEmail, identity.DeviceError, people.Error) as exc:
        raise Refused(str(exc)) from None
    if device is None:
        raise Refused("this machine has no device yet: orch team on makes one")
    me = identity.who_am_i(folks, email)
    if os.path.exists(state.record):
        rec = Record(state.record, readonly=True)
        try:
            projection = snapshot.build(rec, common_dir, chosen["remote"], device, eff, now, __version__, force.blob,
                                        cfg["team_stale_days"])
        finally:
            rec.close()
    else:
        projection = seal.Projection(__version__, force.blob, ())
    outbox = store.Outbox(shadow, chosen["remote"], me.person, device.id, email)
    try:
        return seal.seal(projection, eff, me, device, outbox.next_seq(), now)
    except seal.SealError as exc:
        raise Refused(str(exc)) from None


def task(state, args, confirm):
    from orchestraty import cli
    try:
        session, _, _, _ = cli._claim_session(state, args.session)
    except ValueError as exc:
        raise Refused(str(exc)) from None
    sid, who = session["id"], f"session {session['id'][:8]} ({session['agent']})"
    if args.clear:
        tasks.append(state, sid, clear=True)
        print(f"The ticket and task line of {who} are cleared.")
        return 0
    if args.ticket is not None:
        if not tasks.is_ticket(args.ticket):
            raise Refused("a ticket is KEY-123 (two to ten capital letters and digits, then a number), #123 or GH-123")
        tasks.append(state, sid, ticket=args.ticket)
        print(f"Ticket {args.ticket} for {who}: shared where the policy's keys and share.task allow it.")
        return 0
    if args.from_declared:
        declared = _declared(state, sid)
        if not declared:
            raise Refused("the session's agent declared no task line")
        print(f"The agent declared: {printable(declared)}")
        line = _edited(declared)
        if line is None or not confirm(f"Share this line with your teammates: {printable(line)}?", False):
            print("Nothing stored.")
            return 1
    else:
        line = args.line.strip()
    if not grammar.task_line(line):
        raise Refused("a task line is one line of printable text, at most 120 characters")
    hits = scan.scan(line)
    if hits:
        raise Refused(f"the line looks like it holds a secret ({hits[0].rule}), so it is not stored")
    tasks.append(state, sid, line=line)
    print(f"Task line stored for {who}. It is shared only where the policy says share.task: confirmed.")
    return 0


def _declared(state, sid):
    if not os.path.exists(state.record):
        return ""
    rec = Record(state.record, readonly=True)
    try:
        mine = [d for d in rec.declarations(active_at=time.time_ns()) if d["session_id"] == sid and d.get("task")]
    finally:
        rec.close()
    return mine[-1]["task"] if mine else ""


def _edited(declared):
    """The line the person keeps: the declared one, or what they type instead. None without a terminal."""
    if not (sys.stdin and sys.stdin.isatty()):
        print("No terminal to ask in: nothing stored.")
        return None
    try:
        typed = input("Line to share (Enter keeps it as it is): ").strip()
    except EOFError:
        return None
    return typed or declared


def device(state, args, confirm):
    try:
        if args.new:
            d = identity.Device.new(state)
            print(f"New device {d.id}: this machine's records go under that id from now on. The old device's ref "
                  "stays on the remote until it ages out.")
            return 0
        if args.label is not None:
            d = identity.Device.set_label(state, args.label)
        else:
            d = identity.Device.load(state)
    except (ValueError, identity.DeviceError) as exc:
        raise Refused(str(exc)) from None
    if d is None:
        print("This machine has no device yet: orch team on makes one.")
        return 0
    print(f"device: {d.id}" + (f" ({d.label})" if d.label else " (no label)"))
    return 0


ACTIONS = {"show": show, "on": on, "off": off, "whoami": whoami, "init": init, "check": check, "preview": preview,
           "task": task, "device": device}
