"""Custom-ref push and fetch probe: can a remote keep refs/orchestraty/<person>/<device>?

Status: throwaway (Phase 0). Not part of the product.

    python capture_refprobe.py --repo <root>/repos/<scratch clone> --remote <name, path or URL>
                               --person <p> --device <d> [--root <phase-0 folder>]
                               [--allow-network] [--also-fallback] [--json <out file>]

What it does, for the ref refs/orchestraty/<person>/<device>:
  1. makes a probe commit in the scratch clone with commit-tree: one file, probe.json, holding
     a random nonce, under a probe identity (never the user's name or email). No branch or
     ref in the clone points at it;
  2. checks the ref is not on the remote already (if it is, it reports "exists" and stops: it
     never overwrites or deletes a ref it did not push);
  3. pushes <commit>:<ref> with --no-verify (the clone's own pre-push hooks are not what is
     probed) and reads git's per-ref status (git push --porcelain);
  4. checks with ls-remote that the remote lists the ref at that commit;
  5. fetches the ref into a new, empty bare repository under <root>/repos, as a second device
     would, and checks the commit id and the nonce in probe.json;
  6. deletes the ref on the remote and checks with ls-remote that it is gone. This step runs
     whatever went wrong after the push, so no probe ref is left behind;
  7. removes the temporary repository.
If the remote refuses the custom ref (or with --also-fallback), the fallback, a dedicated
branch refs/heads/orchestraty/<person>/<device>, is probed the same way.

Only scratch clones under <root>/repos are accepted. A local remote must be a bare repository.
Any other remote (https, ssh, git@..., file://host/..., or a \\\\server\\share path) is a network
remote and is refused unless --allow-network is given: every network step of T1 is run by the
owner, who runs this command with that flag. The probe commit's objects stay in the clone,
unreferenced, until git gc removes them.
"""
import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys

import capture_githooks
import capture_hooks
from capture_hooks import Refused

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
PROBE_ID = {"GIT_AUTHOR_NAME": "orchestraty-probe", "GIT_AUTHOR_EMAIL": "probe@orchestraty.invalid",
            "GIT_COMMITTER_NAME": "orchestraty-probe", "GIT_COMMITTER_EMAIL": "probe@orchestraty.invalid"}
ACCEPTED = set(" *+-=")

# Variables that would point git at another repository than the one named on its command line.
REPO_VARIABLES = {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR",
                  "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX"}

def checked_name(value, what):
    if not NAME.match(value or "") or ".." in value or value.endswith(".lock"):
        raise Refused(f"{what} must be letters, digits, '.', '_' or '-' (at most 64), got {value!r}")
    return value


def _run(cwd, args, stdin=b"", extra_env=None, network=False):
    env = {k: v for k, v in os.environ.items() if k not in REPO_VARIABLES}
    if not network:
        env["GIT_TERMINAL_PROMPT"] = "0"
    env.update(extra_env or {})
    proc = subprocess.run(["git", "-c", "maintenance.auto=false", "-c", "gc.auto=0"] + list(args), cwd=cwd,
                          input=stdin, capture_output=True, env=env, timeout=300)
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


def resolve_remote(repo, remote):
    """(URL or path, is_local) for a remote name, a local folder or a URL."""
    url = remote
    if _is_unc(remote):                               # checked first: even os.path.isdir would reach the share
        return url, False
    if not os.path.isdir(remote) and not re.match(r"^[a-z][a-z0-9+.-]*://|^[^/\\]+@[^:]+:", remote):
        code, out, _ = _run(repo, ["remote", "get-url", remote])
        if code != 0:
            raise Refused(f"{remote!r} is not a folder, a URL or a remote of the scratch clone")
        url = out.strip()
    if url.startswith("file://"):
        rest = url[len("file://"):]
        if not rest.startswith("/") or rest.startswith("//"):      # file://host/... or file:////host/...
            return url, False
        path = rest[1:] if re.match(r"^/[A-Za-z]:", rest) else rest
    else:
        path = url
    if _is_unc(path):                                  # a share is a network step, whatever it looks like
        return url, False
    if os.path.isdir(path):
        code, out, _ = _run(path, ["rev-parse", "--is-bare-repository"])
        if code != 0 or out.strip() != "true":
            raise Refused(f"the local remote {path!r} is not a bare repository")
        return os.path.abspath(path), True
    return url, False


def _is_unc(path):
    p = path.replace("/", "\\")
    return p.startswith("\\\\")                        # \\server\share, //server/share, \\?\UNC\...


def probe_commit(repo, nonce):
    code, blob, err = _run(repo, ["hash-object", "-w", "--stdin"],
                           stdin=json.dumps({"orchestraty_probe": nonce}).encode() + b"\n")
    if code != 0:
        raise RuntimeError("hash-object failed: " + err.strip()[:200])
    code, tree, err = _run(repo, ["mktree"], stdin=f"100644 blob {blob.strip()}\tprobe.json\n".encode())
    if code != 0:
        raise RuntimeError("mktree failed: " + err.strip()[:200])
    code, commit, err = _run(repo, ["commit-tree", tree.strip(), "-m", f"orchestraty ref probe {nonce}"],
                             extra_env=PROBE_ID)
    if code != 0:
        raise RuntimeError("commit-tree failed: " + err.strip()[:200])
    return commit.strip()


def push_status(out, ref):
    """("accepted"|"rejected"|"failed", summary) from git push --porcelain output."""
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and len(parts[0]) == 1 and parts[1].endswith(":" + ref):
            if parts[0] in ACCEPTED:
                return "accepted", parts[2].strip()[:200]
            return "rejected", parts[2].strip()[:200]
    return "failed", ""


def listed_at(repo, url, ref, network):
    code, out, _ = _run(repo, ["ls-remote", url, ref], network=network)
    if code != 0:
        return None
    for line in out.splitlines():
        sha, _, name = line.partition("\t")
        if name == ref:
            return sha
    return ""


def _remove_tree(path):
    def retry(func, target, _):
        os.chmod(target, stat.S_IWRITE)                  # git objects are read-only on Windows
        func(target)
    if os.path.isdir(path):
        shutil.rmtree(path, onerror=retry)


def probe_ref(root, repo, url, ref, commit, nonce, network):
    row = {"ref": ref, "push": "not run", "push_summary": "", "listed": False, "fetched": False,
           "nonce_matches": False, "deleted": False, "gone": False}
    existing = listed_at(repo, url, ref, network)
    if existing:                                      # never overwrite or delete a ref someone else put there
        row["push"], row["push_summary"] = "exists", f"already on the remote at {existing}; delete it and rerun"
        return row
    # --no-verify: the clone's own pre-push hooks (the capture chain, husky, lefthook, pre-commit)
    # are not what is being probed, and a failing one would read as the remote refusing the ref.
    code, out, err = _run(repo, ["push", "--porcelain", "--no-verify", url, f"{commit}:{ref}"], network=network)
    row["push"], row["push_summary"] = push_status(out, ref)
    if row["push"] == "failed":
        row["push_summary"] = " ".join(err.strip().splitlines()[-3:])[:300]
    if row["push"] == "rejected":
        return row
    other = os.path.join(root, "repos", f"refprobe-{nonce}.git")          # a second device
    try:
        row["listed"] = listed_at(repo, url, ref, network) == commit
        if row["push"] == "accepted":
            try:
                _run(root, ["init", "-q", "--bare", other])
                _run(other, ["fetch", "-q", "--no-tags", url, f"+{ref}:refs/probe/fetched"], network=network)
                code, sha, _ = _run(other, ["rev-parse", "--verify", "-q", "refs/probe/fetched"])
                row["fetched"] = code == 0 and sha.strip() == commit
                code, text, _ = _run(other, ["cat-file", "blob", f"{commit}:probe.json"])
                try:
                    row["nonce_matches"] = code == 0 and json.loads(text).get("orchestraty_probe") == nonce
                except ValueError:
                    row["nonce_matches"] = False
            finally:
                try:
                    _remove_tree(other)
                except OSError as exc:
                    row["cleanup_error"] = f"{other} left behind: {type(exc).__name__}"
    finally:
        # Whatever happened after the push (a failure, a timeout, Ctrl+C), take the ref off the
        # remote if it may be there: an accepted push, or a failed one that may have landed.
        try:
            if row["push"] == "accepted" or listed_at(repo, url, ref, network):
                code, out, _ = _run(repo, ["push", "--porcelain", "--no-verify", url, f":{ref}"], network=network)
                row["deleted"] = push_status(out, ref)[0] == "accepted"
            row["gone"] = listed_at(repo, url, ref, network) == ""
        except Exception as exc:                   # noqa: BLE001 - reported, and the original error stands
            row["cleanup_error"] = f"could not remove {ref}: {type(exc).__name__}"
    return row


def run(root, repo, remote, person, device, allow_network=False, also_fallback=False):
    root = os.path.abspath(root)
    repo = capture_githooks.checked_repo(root, repo)          # also checks the phase-0 folder's name
    person, device = checked_name(person, "person"), checked_name(device, "device")
    url, local = resolve_remote(repo, remote)
    if not local and not allow_network:
        raise Refused("the remote is not a local folder; network steps are run by the owner with --allow-network")
    nonce = os.urandom(8).hex()
    commit = probe_commit(repo, nonce)
    custom = probe_ref(root, repo, url, f"refs/orchestraty/{person}/{device}", commit, nonce, not local)
    fallback = None
    if custom["push"] == "rejected" or also_fallback:            # not for "exists" or "failed"
        fallback = probe_ref(root, repo, url, f"refs/heads/orchestraty/{person}/{device}", commit, nonce, not local)
    return {"probe_commit": commit, "network": not local, "custom": custom, "fallback": fallback}


def format_report(result):
    out = [f"probe commit | {result['probe_commit']}", f"network remote | {'yes' if result['network'] else 'no'}",
           "which | ref | push | push summary | listed | fetched | nonce matches | deleted | gone"]
    for which in ("custom", "fallback"):
        r = result[which]
        if r is None:
            out.append(f"{which} | not run (the custom ref was {result['custom']['push']})")
            continue
        yes = {True: "yes", False: "no"}
        out.append(" | ".join([which, r["ref"], r["push"], r["push_summary"] or "-"]
                              + [yes[r[k]] for k in ("listed", "fetched", "nonce_matches", "deleted", "gone")]))
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description="Probe whether a remote keeps refs/orchestraty/<person>/<device>.")
    p.add_argument("--root", default=capture_hooks.default_root())
    p.add_argument("--repo", required=True, help="a scratch clone under <root>/repos")
    p.add_argument("--remote", required=True, help="a remote name, a local bare repository or a URL")
    p.add_argument("--person", required=True)
    p.add_argument("--device", required=True)
    p.add_argument("--allow-network", action="store_true", help="the owner's run against a network remote")
    p.add_argument("--also-fallback", action="store_true", help="probe the fallback branch too")
    p.add_argument("--json", help="also write the result as JSON to this file")
    args = p.parse_args(argv)
    try:
        result = run(args.root, args.repo, args.remote, args.person, args.device, args.allow_network,
                     args.also_fallback)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(format_report(result))
    if args.json:
        with open(args.json, "x", encoding="utf-8", newline="\n") as fh:
            json.dump(result, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
