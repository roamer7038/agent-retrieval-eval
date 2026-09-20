"""Shared settings of the gold generators (gen/*.py).

Everything the generators write goes under $ARE_DATA/gold-work (analysis
outputs, candidates, the LLM cache), never into the corpora. The random
choices use SEED and the split name, so running the same generator with
--split test and the same corpora commits yields the held-out set by the
same procedure.
"""
import hashlib
import json
import os
import random
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("ARE_DATA", os.path.join(ROOT, "data"))
CORPORA = os.path.join(DATA, "corpora")
WORK = os.path.join(DATA, "gold-work")
OUT = os.path.join(WORK, "out")      # analyzer outputs
CAND = os.path.join(WORK, "cand")    # candidates per scenario
SEED = 20260920

REPOS = {
    "c1": {"wikictl": "c1/wikictl", "wiki": "c1/wiki"},
    "c2": {"grafana": "c2/grafana"},
    "c3": {"linux": "c3/linux"},
    "c4": {"website": "c4/website"},
}
# The language of the corpus text a question is asked against; questions are
# Japanese, so ja->en means the words of the question do not occur in the
# corpus as they are.
LANG = {"c1": "ja->ja", "c2": "ja->en", "c3": "ja->en", "c4": None}

# Model training cutoff: S2 takes patches merged after it.
CUTOFF = "2026-06-01"


def repo_dir(corpus, repo):
    return os.path.join(CORPORA, REPOS[corpus][repo])


def rng(split, scenario, corpus, salt=""):
    """A generator seeded by the split, scenario and corpus, so that adding a
    corpus or a scenario does not change the draws of the others."""
    h = hashlib.sha256(f"{SEED}:{split}:{scenario}:{corpus}:{salt}".encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def split_of(key, test_share=0.5):
    """Assign an item to dev or test by a hash of a stable key (a function's
    file and name, a commit hash, a page path), so that the two splits never
    share an item whichever order the candidates come in."""
    h = int(hashlib.sha256(f"{SEED}:split:{key}".encode()).hexdigest()[:8], 16)
    return "test" if h / 0xFFFFFFFF < test_share else "dev"


def read_jsonl(path):
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def git(corpus, repo, *args):
    return subprocess.run(["git", "-C", repo_dir(corpus, repo), *args], check=True,
                          capture_output=True, text=True).stdout


def head(corpus, repo):
    return git(corpus, repo, "rev-parse", "HEAD").strip()


def read_lines(corpus, repo, path, start, end):
    with open(os.path.join(repo_dir(corpus, repo), path), errors="replace") as f:
        lines = f.read().split("\n")
    return "\n".join(lines[start - 1:end])
