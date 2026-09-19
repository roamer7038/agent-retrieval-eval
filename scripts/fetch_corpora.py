#!/usr/bin/env python3
"""Clone the pinned corpora of corpora/corpora.yaml into $ARE_DATA/corpora
(default data/corpora; several worktrees can share one $ARE_DATA).

    scripts/fetch_corpora.py [corpus ...]

A repository already at its commit is left alone. The clone keeps the full
history, which the scenarios on change history need.
"""
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("ARE_DATA", os.path.join(ROOT, "data"))
DEST = os.path.join(DATA, "corpora")


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def fetch(corpus, name, spec):
    d = os.path.join(DEST, corpus, name)
    if os.path.isdir(os.path.join(d, ".git")):
        if git("rev-parse", "HEAD", cwd=d) == spec["commit"]:
            print(f"{corpus}/{name}: at {spec['commit'][:12]}")
            return
    else:
        os.makedirs(os.path.dirname(d), exist_ok=True)
        git("clone", "-q", "--no-checkout", spec["url"], d)
    git("-c", "advice.detachedHead=false", "checkout", "-q", spec["commit"], cwd=d)
    print(f"{corpus}/{name}: checked out {spec['commit'][:12]}")


def main():
    with open(os.path.join(ROOT, "corpora", "corpora.yaml")) as f:
        corpora = yaml.safe_load(f)
    wanted = sys.argv[1:] or list(corpora)
    for corpus in wanted:
        if corpus not in corpora:
            raise SystemExit(f"unknown corpus: {corpus}")
        for name, spec in corpora[corpus]["repos"].items():
            fetch(corpus, name, spec)


if __name__ == "__main__":
    main()
