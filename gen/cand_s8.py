#!/usr/bin/env python3
"""Candidates for S8 (confirm that something does not exist): a real name
(a Go function, a feature toggle, a feature gate, a wikictl command) with one
word swapped, kept only when the new name occurs nowhere in the corpus
(checked over every text file, case-insensitively, also with the words
split by '_', '-' or ' '). The gold is null.

    gen/cand_s8.py [--split dev]

The name is absent by construction; what a person has to check is that the
paraphrase does not describe something that does exist under another name.
Three candidates are drawn for every question wanted (N), because the
paraphrase of a name that does not exist is thrown away about seven times out
of ten: asked without the name, the question drifts towards the real thing
(PE2, results/pe2-gold.md section 4).
Output: gold-work/cand/<split>/S8.jsonl
"""
import argparse
import collections
import os
import re

from common import CAND, OUT, REPOS, dropped_base_ids, repo_dir, read_jsonl, rng, split_of, write_jsonl

SWAP = {"Get": "Archive", "List": "Merge", "Create": "Clone", "Delete": "Freeze", "Update": "Rebalance",
        "Check": "Compress", "Parse": "Encrypt", "Validate": "Throttle", "Build": "Shard", "Load": "Snapshot",
        "Find": "Mirror", "Add": "Quarantine", "Remove": "Quarantine", "Read": "Replay", "Write": "Replay",
        "Front": "Back", "Vertical": "Horizontal", "Pod": "Node", "Start": "Hibernate", "Stop": "Hibernate",
        "Split": "Braid", "Resolve": "Scramble",
        "Enable": "Hibernate", "Search": "Replay", "Render": "Compress", "Export": "Hibernate"}
TEXT_EXT = (".go", ".ts", ".tsx", ".js", ".md", ".yaml", ".yml", ".json", ".ini", ".toml", ".txt", ".html", ".c", ".h",
            ".rst", ".cue", ".sh")
# 3 candidates per question wanted: the paraphrase of about 7 in 10 is thrown
# away, and the other scenarios keep 11-26 base questions
N = {"c1": 18, "c2": 18, "c4": 18}
_TEXT = {}


def corpus_text(corpus):
    if corpus not in _TEXT:
        parts = []
        for repo in REPOS[corpus]:
            base = repo_dir(corpus, repo)
            for dp, dn, fn in os.walk(base):
                dn[:] = [x for x in dn if x not in (".git", "node_modules")]
                for f in fn:
                    if f.endswith(TEXT_EXT):
                        try:
                            parts.append(open(os.path.join(dp, f), errors="replace").read().lower())
                        except OSError:
                            pass
        _TEXT[corpus] = "\n".join(parts)
    return _TEXT[corpus]


def words(name):
    return re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+", name)


def absent(corpus, name):
    t = corpus_text(corpus)
    w = [x.lower() for x in words(name)]
    forms = {name.lower(), "_".join(w), "-".join(w), " ".join(w), "".join(w)}
    return not any(f in t for f in forms if len(f) >= 6)


# Words a swap must not break: kinds and terms whose parts mean nothing alone.
COMPOUNDS = re.compile(r"StatefulSet|DaemonSet|ReplicaSet|ConfigMap|ReadOnly|ReadWrite|WriteOnce|ReadMany")


def mutate(name, r):
    """Swap one word for one that changes what the thing does. Swaps that
    only negate (Has/Lacks), rename (New/Forked) or break a compound word
    are left out: a person reads them as the real thing under another name."""
    if COMPOUNDS.search(name):
        return None, None
    ws = words(name)
    idx = [i for i, x in enumerate(ws) if x in SWAP or x.capitalize() in SWAP]
    if not idx:
        return None, None
    i = r.choice(idx)
    old = ws[i]
    new = SWAP.get(old) or SWAP[old.capitalize()].lower()
    if old[0].islower():
        new = new[0].lower() + new[1:]
    ws2 = ws[:i] + [new] + ws[i + 1:]
    return "".join(ws2), (old, new)


def go_items(corpus, split, n, repo):
    defs, seen = [], set()
    for d in read_jsonl(os.path.join(OUT, f"{corpus}-go.jsonl")):
        if d["rec"] != "def" or d["test"] or d["kind"] not in ("func", "method") or len(d["doc"]) < 40:
            continue
        if corpus == "c2" and not d["file"].startswith("pkg/"):
            continue
        if (d["file"], d["id"]) in seen:
            continue
        seen.add((d["file"], d["id"]))
        if split_of(f"{corpus}:S8:{d['file']}:{d['id']}") == split:
            defs.append(d)
    r = rng(split, "S8", corpus, "go")
    r.shuffle(defs)
    out, dropped = [], dropped_base_ids()
    for d in defs:
        if len(out) == n:
            break
        fake, sw = mutate(d["name"], r)
        if not fake or not absent(corpus, fake):
            continue
        if f"{split}-{corpus}-s8-go-{fake}" in dropped:
            continue
        what = f"型 `{d['recv']}` のメソッド `{fake}`" if d["recv"] else f"関数 `{fake}`"
        out.append(item(corpus, split, f"go-{fake}", f"{repo} のソースコードで、{what} はどのファイルに定義されているか。",
                        fake, d["id"], d["doc"], sw, "関数",
                        f"{repo} のソースコードで、次の架空の関数がどのファイルに定義されているかを尋ねる質問",
                        f"{repo} リポジトリの根からのファイルのパス"))
    return out


def toggles_c2(split, n):
    p = os.path.join(repo_dir("c2", "grafana"), "pkg/services/featuremgmt/registry.go")
    txt = open(p).read()
    names = re.findall(r'Name:\s+"([A-Za-z0-9]+)",\s*\n\s*Description:\s+"([^"]+)"', txt)
    names = [x for x in names if split_of(f"c2:S8:toggle:{x[0]}") == split]
    r = rng(split, "S8", "c2", "toggle")
    r.shuffle(names)
    out, dropped = [], dropped_base_ids()
    for name, desc in names:
        if len(out) == n:
            break
        fake, sw = mutate(name[0].upper() + name[1:], r)
        if not fake:
            continue
        fake = fake[0].lower() + fake[1:]
        if not absent("c2", fake) or f"{split}-c2-s8-toggle-{fake}" in dropped:
            continue
        out.append(item("c2", split, f"toggle-{fake}",
                        f"Grafana の feature toggle `{fake}` は、何を有効にするものか。", fake, name, desc, sw,
                        "feature toggle", "Grafana の、次の架空の機能の切り替え（feature toggle）が何を有効にするかを尋ねる質問",
                        "説明を 1 文で"))
    return out


def gates_c4(split, n):
    d = os.path.join(repo_dir("c4", "website"), "content/en/docs/reference/command-line-tools-reference/feature-gates")
    gates = []
    for f in sorted(os.listdir(d)):
        if f.endswith(".md") and not f.startswith("_"):
            body = open(os.path.join(d, f)).read().split("\n---", 1)[-1].strip().split("\n")[0]
            gates.append((f[:-3], body))
    gates = [g for g in gates if split_of(f"c4:S8:gate:{g[0]}") == split]
    r = rng(split, "S8", "c4", "gate")
    r.shuffle(gates)
    out, dropped = [], dropped_base_ids()
    for name, desc in gates:
        if len(out) == n:
            break
        fake, sw = mutate(name, r)
        if not fake or not absent("c4", fake) or f"{split}-c4-s8-gate-{fake}" in dropped:
            continue
        out.append(item("c4", split, f"gate-{fake}",
                        f"Kubernetes の feature gate `{fake}` は、どの版でベータになったか。", fake, name, desc, sw,
                        "feature gate", "Kubernetes の、次の架空の機能の切り替え（feature gate）がどの版でベータになったかを尋ねる質問",
                        "版（例: `1.30`）"))
    return out


def item(corpus, split, key, q, fake, real, doc, sw, kind, ptask, fmt):
    return {
        "base_id": f"{split}-{corpus}-s8-{key}", "corpus": corpus, "scenario": "S8", "split": split,
        "q_ident": q, "identifiers": [fake],
        "subject": {"label": fake, "kind": kind, "real": real, "real_doc": doc, "swap": sw,
                    "doc": f"実在の {real}（{doc}）の「{sw[0]}」を「{sw[1]}」に変えた架空のもの"},
        "paraphrase_task": ptask, "answer_format": fmt,
        "gold": {"type": "null"}, "evidence_accept": [],
        "review": {"required": True,
                   "check": "言い換えた質問が、実在の別の機能（名前の違うもの）を指していないか"},
        "source": {"method": "one word of a real name swapped; the new name occurs in no text file of the corpus"},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    a = ap.parse_args()
    rows = go_items("c1", a.split, N["c1"], "wikictl")
    rows += go_items("c2", a.split, N["c2"] // 2, "grafana") + toggles_c2(a.split, N["c2"] - N["c2"] // 2)
    rows += gates_c4(a.split, N["c4"])
    write_jsonl(os.path.join(CAND, a.split, "S8.jsonl"), rows)
    print("S8", collections.Counter(r["corpus"] for r in rows))


if __name__ == "__main__":
    main()
