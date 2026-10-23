"""The time from installing Orchestraty to the first note, on a large repository. The plan's pass
check is under 10 minutes on a 10,000-file repository, on Windows and macOS. This script is not
packaged: it runs from a checkout, with the wheel built first (python -m build).

    python scripts/first_note_timing.py [--wheel dist/orchestraty-<version>-py3-none-any.whl]
                                        [--files 10000 | --repo PATH] [--keep]

In a temporary folder it

1. makes a fresh virtual environment and installs the wheel (nothing is downloaded: the wheel
   has no dependencies);
2. generates a repository of --files Python files (10,000 by default), or clones the one --repo
   names (the clone is what changes, never that repository), with a.py and b.py, the scripted
   sessions' two files, committed on top;
3. runs orch init --yes, with its own agent config folder and state folder;
4. plays the two scripted sessions of scripts/smoke.py: session A changes the contract of
   charge() in a.py, and session B waits for its note before it edits b.py;
5. prints the time each step took, and the time from the install to the first note. Making the
   repository is left out of that sum, since in real use the repository is there already.

The first note can only come once the coordinator has indexed the base commit, so on a large
repository that index is most of the time.
"""
import argparse
import glob
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke  # noqa: E402 - smoke.py, beside this script

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS_SECONDS = 600
WAIT_SECONDS = 3600


def newest_wheel():
    found = sorted(glob.glob(os.path.join(ROOT, "dist", "orchestraty-*-py3-none-any.whl")), key=os.path.getmtime)
    return found[-1] if found else None


def tracked_files(repo, env):
    return len(smoke.git(repo, "ls-files", "-z", env=env).stdout.split(b"\0")) - 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Time Orchestraty from install to the first note.")
    parser.add_argument("--wheel", help="the wheel to install (default: the newest in dist/)")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--files", type=int, default=10000, help="the files of the generated repository (10000)")
    where.add_argument("--repo", help="clone this repository instead of generating one")
    parser.add_argument("--wait", type=float, default=WAIT_SECONDS, help="seconds to wait for the note at most")
    parser.add_argument("--keep", action="store_true", help="keep the temporary folder, and say where it is")
    args = parser.parse_args(argv)
    wheel = args.wheel or newest_wheel()
    if not wheel or not os.path.isfile(wheel):
        parser.error("no wheel: build it first (python -m build), or name one with --wheel")
    folder = os.path.realpath(tempfile.mkdtemp(prefix="orch-timing-"))
    steps = smoke.Steps()
    left_out = []
    try:
        env = smoke.isolated_env(folder)
        with steps("a fresh virtual environment"):
            python = smoke.make_venv(os.path.join(folder, "venv"), env)
        with steps("pip install " + os.path.basename(wheel)):
            smoke.install_wheel(python, wheel, env)
        made = f"clone {args.repo}" if args.repo else f"generate a repository of {args.files} files"
        with steps(made):
            repo, wt_a, wt_b = smoke.make_repository(folder, env, filler=max(0, args.files - 2), source=args.repo)
        left_out.append(made)
        steps.say(f"     {tracked_files(repo, env)} files in {repo}")
        smoke.play([smoke.venv_file(os.path.join(folder, "venv"), "orch")], repo, wt_a, wt_b, env, steps, args.wait)
    except smoke.SmokeError as exc:
        print(f"\nThe timing run failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if args.keep:
            print(f"Kept: {folder}")
        elif not smoke.remove_tree(folder):
            print(f"Could not delete all of {folder}.", file=sys.stderr)
    width = max(len(name) for name, _ in steps.taken)
    print("\nFrom install to the first note, step by step:")
    total = 0.0
    for name, seconds in steps.taken:
        if name in left_out:
            print(f"  {name:<{width}}  {seconds:8.1f} s  (left out: the repository is there already in real use)")
            continue
        total += seconds
        print(f"  {name:<{width}}  {seconds:8.1f} s")
    verdict = "within" if total < PASS_SECONDS else "over"
    print(f"  {'install to first note':<{width}}  {total:8.1f} s  ({verdict} the {PASS_SECONDS // 60}-minute pass check)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
