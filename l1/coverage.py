#!/usr/bin/env python3
"""What kind of file each tool's index holds, and which contrasts that rules out.

The decision of 2026-09-20 was: a tool whose index holds no file of the kind a
corpus's gold is made of cannot answer that corpus at all, so its 0.000 there
says what the index covers and not how good the tool is; the cell is measured
and reported but left out of the comparisons. PE3b applied that to c4 alone,
because c4's gold is Markdown throughout. The same three tools index no
Markdown in c1 or c2 either, where S4, S5 and part of S7 and S9 also have a
gold made only of Markdown (pe3b-l1.md, sections 4 and 11 #4), so PE3b's
contrasts were still comparing "can the index reach the answer" with "is the
tool any good".

PE3c makes the rule uniform and per question:

    a (tool, corpus, question) contrast is left out when no relevant file of
    the question is of a kind the tool's index holds.

The gold of this experiment is made of code (.go/.ts/.tsx/.c/.h) and of
Markdown (.md) and of nothing else, so the rule needs two facts per cell: does
the index hold Markdown, and does it hold anything but Markdown. Both are
counted from the index PE1 built (`l1/coverage.py count`), not guessed:

  NO_MARKDOWN    the index holds no .md, so a question whose gold is all
                 Markdown is out of reach (global, semble, codegraph, Serena).
  ONLY_MARKDOWN  the index holds nothing but .md, so a question with no
                 Markdown in its gold is out of reach (qmd, which is a
                 Markdown search tool: its store pattern is `**/*.md`).

The second half is the same rule read the other way round. Leaving it out
would let the rule excuse the tools that index only code while charging the
tool that indexes only documents for the same thing.

A cell that is left out whatever the question (every question of the corpus is
excluded) is still named in l1/run.py's NOT_COMPARABLE, so that `plan` and the
applicability table keep showing it.
"""
import collections
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tasks as T  # noqa: E402

INDEXES = os.path.join(T.DATA, "indexes")

# (tool, corpus) -> why the index holds no Markdown. Counted from the index
# PE1 built; `l1/coverage.py count` prints the counts again.
NO_MARKDOWN = {
    ("global", "c1"): "GPATH に並ぶ 64 件に .md が 0 件（wiki は索引が空）",
    ("global", "c2"): "GPATH に並ぶ 11,264 件に .md が 0 件",
    ("global", "c4"): "GPATH に並ぶ 1,299 件に .md が 0 件（svg 583・html 520 など）",
    ("semble", "c1"): "索引の塊 1,198 件に .md が 0 件（.go 1,195・.sh 3）",
    ("semble", "c2"): "索引の塊 192,343 件に .md が 0 件（go・ts・tsx・sql・cue・js）",
    ("semble", "c4"): "索引の塊 7,379 件に .md が 0 件（js・css・py・scss・go・sh）",
    ("codegraph", "c1"): "索引したファイル 66 件に .md が 0 件（wiki は 0 件）",
    ("codegraph", "c2"): "索引したファイル 16,369 件に .md が 0 件",
    ("codegraph", "c4"): "索引したファイル 1,661 件に .md が 0 件（yaml 1,557 ほか）",
    ("serena", "c1"): "言語サーバの索引 62 件はすべて .go（wiki には .serena が無く、丸ごと対象外）",
    ("serena", "c2"): "言語サーバの索引 6,619 件はすべて .go（統括の決定で c2 は Go のみ）",
}
# (tool, corpus) -> 索引に Markdown しか入らない理由。同じ規則の裏側。
ONLY_MARKDOWN = {
    ("qmd", "c1"): "索引 137 件はすべて .md（qmd の対象は `**/*.md`。.go 62 件は索引外）",
    ("qmd", "c2"): "索引 1,031 件はすべて .md（.go 6,619 件・.ts 4,736 件は索引外）",
    ("qmd", "c4"): "索引 8,229 件はすべて .md",
}
MARKDOWN = ".md"


def gold_kinds(qrels):
    return {os.path.splitext(p)[1].lower() for p in qrels}


def not_comparable(tool, corpus, qrels):
    """None、または「この問をこの組の対比から外す」理由。

    正解のどのファイルも索引に無い種類のとき、その問はその道具には届かない。
    """
    kinds = gold_kinds(qrels)
    if not kinds:
        return None
    if kinds == {MARKDOWN} and (tool, corpus) in NO_MARKDOWN:
        return NO_MARKDOWN[(tool, corpus)]
    if MARKDOWN not in kinds and (tool, corpus) in ONLY_MARKDOWN:
        return ONLY_MARKDOWN[(tool, corpus)]
    return None


def reason(tool, corpus):
    return NO_MARKDOWN.get((tool, corpus)) or ONLY_MARKDOWN.get((tool, corpus)) or ""


# --- 索引の中身を数え直す -------------------------------------------------
def _semble(corpus):
    base = os.path.join(INDEXES, "semble", corpus, "index", "semble")
    ext = collections.Counter()
    for h in sorted(os.listdir(base)):
        p = os.path.join(base, h, "index", "chunks.json")
        if os.path.exists(p):
            with open(p) as f:
                for ch in json.load(f):
                    ext[os.path.splitext(ch.get("file_path", ""))[1].lower()] += 1
    return ext


def _codegraph(corpus):
    up = os.path.join(INDEXES, "codegraph", corpus, "upper")
    ext = collections.Counter()
    for repo in sorted(os.listdir(up)):
        db = os.path.join(up, repo, ".codegraph", "codegraph.db")
        if not os.path.exists(db):
            continue
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        cols = [r[1] for r in con.execute("pragma table_info(files)")]
        col = "path" if "path" in cols else cols[1]
        for (p,) in con.execute(f"select {col} from files"):
            ext[os.path.splitext(p or "")[1].lower()] += 1
        con.close()
    return ext


def _ctags(corpus):
    base = os.path.join(INDEXES, "ctags", corpus, "index")
    ext = collections.Counter()
    for fn in sorted(os.listdir(base)):
        if not fn.endswith(".tags"):
            continue
        seen = set()
        with open(os.path.join(base, fn), errors="replace") as f:
            for line in f:
                if line.startswith("!_TAG"):
                    continue
                p = line.split("\t")[1:2]
                if p:
                    seen.add(p[0])
        for p in seen:
            ext[os.path.splitext(p)[1].lower()] += 1
    return ext


# global は GTAGS が BSD の DB なのでコンテナの中の global で数える。
GLOBAL_CMD = ("l1/run.py start global <corpus> のあと、"
              "docker exec are-l1-global-<corpus> sh -c "
              "'for r in $REPOS; do cd /work/$r; GTAGSDBPATH=/index/$r GTAGSROOT=/work/$r "
              "global -P \".\" | sed \"s/.*\\\\.//\" | sort | uniq -c; done'")

READERS = {"semble": _semble, "codegraph": _codegraph, "ctags": _ctags}


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "rule"
    if what == "count":
        print("| 道具 | 題材 | 索引のファイル（塊）数 | .md | 多い拡張子 |")
        print("|---|---|--:|--:|---|")
        for tool, reader in READERS.items():
            for c in ("c1", "c2", "c3", "c4"):
                d = os.path.join(INDEXES, tool, c)
                if not os.path.isdir(d):
                    continue
                try:
                    ext = reader(c)
                except Exception as e:  # noqa: BLE001
                    print(f"| {tool} | {c} | ― | ― | {e!r} |")
                    continue
                top = "・".join(f"{k or '(なし)'} {v}" for k, v in ext.most_common(5))
                print(f"| {tool} | {c} | {sum(ext.values())} | {ext.get('.md', 0)} | {top} |")
        print(f"\nglobal は GTAGS が BSD の DB なので次で数える:\n    {GLOBAL_CMD}")
        return
    # rule: 規則が外す対比の一覧
    ts = T.load()
    import run as R  # noqa: PLC0415
    rows = collections.Counter()
    for t in ts:
        for tool, spec in R.TOOLS.items():
            if t["corpus"] not in spec["corpora"]:
                continue
            if not_comparable(tool, t["corpus"], t["qrels"]):
                rows[(tool, t["corpus"], t["scenario"])] += 1
    print("| 道具 | 題材 | シナリオ | 外した問 | 理由 |")
    print("|---|---|---|--:|---|")
    for (tool, c, s), n in sorted(rows.items()):
        print(f"| {tool} | {c} | {s} | {n} | {reason(tool, c)} |")
    print(f"\n外した対比 {sum(rows.values())} 件")


if __name__ == "__main__":
    main()
