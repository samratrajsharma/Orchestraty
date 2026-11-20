"""orch doctor's team checks, for the repository doctor runs in: offline, from what ``orch team on``
and team sync's fetches kept. Doctor never fetches, pushes or runs the anonymous-read probe; it
shows the probe's last answer.

Where team sync is off but the repository's own ``origin/HEAD`` has a policy, the only line is a
note that it can be turned on. Where it is on: the git email; the policy in force (it reads, its
version and deny kinds are known, sync, visibility and public_sync agree); the people file and
whether it lists you; the last probe against the policy's visibility; a policy that now allows
more than you agreed to; a working copy of the policy that differs from the one in force; the
secret scanner's self-test; a credential helper or ssh command set in the repository's own config,
which Orchestraty's fetches and pushes do not use; and a shallow or partial clone.
"""
from orchestraty import config, gitio, remote, team
from orchestraty.team import identity, people, policy, probe, scan, view


def checks(state, cwd, ok, problem, note):
    """The team checks for the repository holding `cwd`, made with doctor's ok, problem and note."""
    try:
        info = gitio.worktree_info(cwd)
    except (gitio.GitError, gitio.UntrustedRepositoryError):
        return
    if not info:
        return
    common_dir, top = info["common_dir"], info["top"]
    chosen = team.settings(config.load(state), common_dir)
    name = chosen["remote"]
    if not chosen["on"]:
        if _git_ok(["cat-file", "-e", f"refs/remotes/{name}/HEAD:{policy.FILE}"], common_dir):
            yield note("this repository's policy shares with teammates: orch team on")
        return
    try:
        email = identity.my_email(common_dir)
    except identity.NoEmail as exc:
        yield problem("team: no git email is set for this repository, so team sync stays off there", exc.fix)
        email = None
    shadow = remote.Shadow(state, common_dir)
    try:
        force = policy.in_force(shadow, name)
    except (gitio.GitError, remote.ShadowBroken):
        force = None
    if force is None:
        yield problem(f"team policy: none is known on {name}'s default branch", "orch team on reads it again")
        return
    off = force.off()
    if off:
        yield problem(f"team policy: {off[0]}", off[1])
    else:
        yield ok(f"team policy: in force on {name}'s default branch ({force.blob[:7]}), {force.visibility}")
    for text in force.notes:
        yield note(f"team policy: {text}")
    for key in force.ignored:
        yield note(f"team policy: {key} is not a setting this version knows, so it grants nothing")
    yield from _people(shadow, name, email, force, problem, note, ok)
    answer, _ = probe.last(shadow)
    if answer is True and force.visibility == "private":
        why, fix = policy.PROBE_PUBLIC
        yield problem(f"team policy: {why}", fix)
    agreed = policy.from_normalised(chosen["consented"]) if chosen["consented"] else None
    widened = policy.widened(force, agreed) if agreed is not None else []
    if widened:
        yield note(f"team policy: it now allows more than you agreed to ({'; '.join(widened)}): orch team on to "
                   "review it")
    mine = _working_blob(common_dir, top)
    if mine and force.blob and mine != force.blob:
        yield note("team policy: the copy in your working copy differs from the one in force; it counts once it is "
                   "merged into the default branch")
    failed = scan.self_test()
    if failed:
        yield problem(f"secret scanner: its self-test fails ({'; '.join(failed)})",
                      "reinstall Orchestraty: pipx reinstall orchestraty (or uv tool install --reinstall orchestraty)")
    else:
        yield ok("secret scanner: its self-test passes")
    local = _local_config(common_dir)
    if local:
        yield note(f"team: {' and '.join(local)} set in this repository's own config, which Orchestraty's fetches and "
                   "pushes do not use; set it in your global git config if they need it")
    if _git_out(["rev-parse", "--is-shallow-repository"], common_dir) == "true":
        yield note("team: a shallow clone, so a teammate's branch may need history this clone lacks")
    if _git_out(["config", "--get", f"remote.{name}.promisor"], common_dir) == "true":
        yield note("team: a partial clone, so reading a teammate's branch may lack objects this clone has not fetched")


def _people(shadow, name, email, force, problem, note, ok):
    try:
        folks = view.people_in_force(shadow, name)
    except people.Error as exc:
        yield problem(f"people file: it cannot be read ({exc}), so team sync stays off", "fix .orchestraty/people.yml")
        return
    folks = folks if folks is not None else people.People.empty()
    if email is None:
        return
    me = identity.who_am_i(folks, email)
    if me.listed:
        yield ok(f"people file: you are {me.person}")
    elif force.unlisted == "refuse":
        yield problem("people file: you are not in .orchestraty/people.yml, and the policy shares only for the people "
                      "it lists", "add yourself to .orchestraty/people.yml (orch team whoami prints the lines)")
    else:
        yield note(f"people file: you are not in .orchestraty/people.yml, so you share as {me.person}; orch team "
                   "whoami prints the lines to add")


def _working_blob(common_dir, top):
    """The blob id of the working copy's policy file, or "" when it has none."""
    import os
    path = os.path.join(top, *policy.FILE.split("/"))
    if not os.path.isfile(path):
        return ""
    return _git_out(["hash-object", "--", path], common_dir)


def _local_config(common_dir):
    found = []
    if _git_out(["config", "--local", "--get-regexp", r"^credential\."], common_dir):
        found.append("a credential helper")
    if _git_out(["config", "--local", "--get", "core.sshCommand"], common_dir):
        found.append("core.sshCommand")
    return found


def _git_out(args, cwd):
    try:
        return gitio.run(args, cwd).strip()
    except gitio.GitError:
        return ""


def _git_ok(args, cwd):
    try:
        gitio.run(args, cwd)
    except gitio.GitError:
        return False
    return True
