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
import re
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

# Files written for tests: the tests themselves and the stand-ins they use
# (fakes, mocks, stubs, test data, helpers). A question that says it leaves
# tests out leaves these out too, and the gold of S3 and S7 is built the same
# way, so that the words of the question and the gold say the same thing.
# The list is closed on purpose: the question names it, so an answer can be
# decided without reading this file.
# The name of a file counts too, not only the directory it sits in (PE2d):
# a file called testing.go, test_utils.go or testUtils.ts holds helpers for
# the tests of its package, and grafana has 29 of the first alone. The names
# are a closed list rather than "the name begins with test", because
# grafana's tester.go and testresults.go and linux's testmode.c are ordinary
# code whose name begins that way.
TEST_PATH = re.compile(r"""(?ix)
      _test\.(go|c|py)$ | \.(test|spec)\.[jt]sx?$ | (^|/)test_[^/]+\.(c|py)$
    | (^|/)(fake|mock|stub|dummy)[a-z0-9_]*\.(go|ts|tsx)$
    | (^|/)[a-z0-9_]*_(fake|mock|stub)\.(go|ts|tsx)$
    | (^|/)test([_-]?(s|ing|util|utils|helper|helpers|support|env|setup|data|fixture|fixtures))?\.(go|c|py|ts|tsx)$
    | (^|/)self[_-]?tests?\.(go|c|py)$
    | (^|/)fixtures?\.(go|c|py|ts|tsx)$ | \.fixtures?\.[jt]sx?$
    | (^|/)(test(s|ing|data|util|utils|helper|helpers|support|case|cases|fixtures)?
           |fake|fakes|mock|mocks|stub|stubs|fixture|fixtures|__tests__|__mocks__|__fixtures__
           |e2e|e2e-playwright|selftests|kunit)(/|$)
""")
# How the questions of S3 and S7 say which files they leave out. This wording
# and TEST_PATH above are meant to say the same thing, so that the gold of a
# question can be worked out from the question alone.
TEST_EXCL_JA = ("テストとテスト用の代用品のファイル（名前が `_test.go`・`*.test.tsx`・`*.spec.ts` のように終わるもの、"
                "`mock`・`fake`・`stub` で始まるか `_mock.go`・`_fake.go`・`_stub.go` のように終わるもの、"
                "名前そのものが `test`・`tests`・`testing`・`testutil(s)`・`testhelper(s)`・`testsupport`・"
                "`testenv`・`testsetup`・`testdata`・`selftest`・`fixture(s)` であるもの"
                "（`_`・`-` の有無と大文字・小文字は問わない）、"
                "`testdata/`・`fakes/`・`mocks/`・`testing/`・`e2e/` などの場所にあるもの）を除く")
TEST_EXCL_EN_NOTE = ("tests and their stand-ins (fakes, mocks, stubs, test data, and files whose own "
                     "name is testing, testutil, testhelper and the like) are left out")


def is_test_path(path):
    """True for a test file or a file that exists to stand in for one."""
    return bool(TEST_PATH.search(str(path)))


_DROPPED = None


def dropped_base_ids():
    """Candidates whose gold an audit found wrong, or whose question the audit
    found invalid (results/pe2-audit.jsonl). The generators skip them and draw
    another candidate in their place, so a question that was thrown away does
    not come back when the generators run again. The last record of a base
    decides, and the verdict "regenerate" brings one back: PE2c uses it for
    the S4 bases of c1 whose section and key points an audit passed and whose
    paraphrase alone was the problem, where the corpus holds no other
    candidate to draw (results/pe2c.md)."""
    global _DROPPED
    if _DROPPED is None:
        _DROPPED = set()
        path = os.path.join(ROOT, "results", "pe2-audit.jsonl")
        if os.path.exists(path):
            last = {}
            with open(path) as f:
                for line in f:
                    if line.strip():
                        a = json.loads(line)
                        last[a["base_id"]] = a["verdict"]
            _DROPPED = {b for b, v in last.items() if v in ("error", "invalid")}
    return _DROPPED


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
