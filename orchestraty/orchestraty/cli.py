"""The ``orch`` command line. Only ``--version`` exists so far; commands arrive with the
components they drive."""
import argparse

from orchestraty import __version__


def build_parser():
    parser = argparse.ArgumentParser(
        prog="orch",
        description="Keeps parallel coding-agent sessions aware of each other.",
    )
    parser.add_argument("--version", action="version", version=f"orch {__version__}")
    return parser


def main(argv=None):
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
