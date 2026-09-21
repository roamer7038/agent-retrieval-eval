#!/usr/bin/env python3
"""L1: the questions, the query forms and the relevant files (qrels).

The rules here are the ones that go into the pre-registration; they are
mechanical so that L1 is deterministic and has no LLM in it.

Query forms
-----------
A question is turned into at most three forms. Which form a tool gets is
fixed by the tool's query interface (see l1/run.py: TOOLS), not by the
question.

  ident  the identifiers of the question, in the order they appear.
         Identifiers are the tokens inside backticks that match
         [A-Za-z_][A-Za-z0-9_]{2,} and are not a language keyword
         (struct, type, func, ...), followed by the bare tokens of the
         question that are snake_case or CamelCase and at least 4 long.
         A backticked span that names a path (it holds a "/") is skipped:
         the question gives it to the reader as a locator, not as the
         symbol being asked about.
         An identifier that appears in BOILERPLATE_MIN_DOCS or more of the
         questions is boilerplate of the question template (the list of
         file-name patterns that says which files to leave out) rather than
         the symbol being asked about, and is dropped: see `boilerplate`.
         A question without an identifier gives an empty form: a tool that
         takes only identifiers then returns nothing (recorded as
         empty_query, and reported as a property of the tool, not as a
         failure).
  terms  the identifiers, plus the content words of the question: for ja,
         runs of two or more kanji/katakana characters that are not in the
         boilerplate stop list; for en, the words of at least three ASCII
         letters that are not in the English stop list.
  nat    the question as it stands (one string).

Language
--------
The question language of the experiment is Japanese, so the primary form of
every text tool is the Japanese one (query_lang=ja). The confirmed English
translation is run as a second condition (query_lang=en) so that the
disadvantage of a Japanese question against an English corpus can be
measured instead of assumed. Identifier forms are ASCII either way and are
run once (query_lang=id).

Relevant files (qrels)
----------------------
The unit of L1 is a file, written as <repo>/<path within the repository>,
which is what tasks/dev/pe2.jsonl already uses in `evidence`. The relevant
set of a question is its `evidence`, with a directory entry (S7) expanded to
the files under it. Every relevant file has grade 1: the gold does not rank
its evidence.

S6 and S8 are not part of L1 (plan v3.1).
"""
import collections
import hashlib
import json
import os
import re
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("ARE_DATA", os.path.join(ROOT, "data"))
CORPORA = os.path.join(DATA, "corpora")
TASKS = os.path.join(ROOT, "tasks", "dev", "pe2.jsonl")

L1_SCENARIOS = ("S1", "S2", "S3", "S4", "S5", "S7", "S9")

# Keywords that appear inside backticks next to the identifier that is meant.
KEYWORDS = {
    "struct", "type", "func", "class", "const", "var", "enum", "interface",
    "package", "import", "return", "static", "void", "int", "string", "bool",
    "true", "false", "null", "nil", "none",
}
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
BACKTICK = re.compile(r"`([^`]+)`")
SNAKE_OR_CAMEL = re.compile(r"\b[A-Za-z_]+_[A-Za-z0-9_]*\b|\b[a-z]+[A-Z][A-Za-z0-9]*\b|\b[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*\b")
JA_RUN = re.compile(r"[々一-鿿ァ-ヶー]{2,}")
EN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{2,}")

# Boilerplate of the question templates: words that say what kind of answer is
# wanted rather than what the answer is about.
JA_STOP = {
    "ソースコード", "ファイル", "パス", "リポジトリ", "定義", "関数", "型", "メソッド",
    "変数", "実装", "処理", "以下", "場合", "説明", "記述", "内容", "対象", "一覧",
    "全て", "すべて", "直下", "文書", "文章", "ページ", "部分", "箇所", "位置", "名前",
    "指定", "利用", "使用", "確認", "取得", "設定", "理由", "手順", "方法", "何", "誰",
    "呼", "出", "挙", "示", "書", "含", "持", "行", "数", "版", "的",
}
EN_STOP = {
    "the", "and", "for", "are", "which", "what", "where", "how", "does", "did",
    "file", "files", "path", "source", "code", "repository", "repo", "function",
    "functions", "method", "methods", "type", "types", "variable", "defined",
    "define", "definition", "implements", "implemented", "implementation",
    "that", "this", "with", "from", "into", "all", "list", "root", "example",
    "its", "his", "her", "was", "were", "been", "being", "have", "has", "had",
    "can", "could", "would", "should", "you", "your", "one", "two", "who",
    "when", "why", "whose", "there", "their", "them", "then", "than", "such",
    "any", "each", "some", "not", "but", "also", "only", "e.g", "i.e",
}
# The name of the corpus or of its repositories says which corpus the question
# is about, not what the answer is; as a term it matches everywhere.
CORPUS_WORDS = {"wikictl", "wiki", "grafana", "linux", "kubernetes", "website",
                "k8s", "docs", "doc"}
MAX_TERMS = 12

# PE2d は「テストを除く」の規則を質問の本文に書き、除くファイル名の型を
# バッククォートで並べた（`_test.go`・`mock`・`testdata` ...）。識別子の
# 取り出しはその全部を拾うので、質問 1 つにつき識別子が 21 個になり、
# 本当に問われている記号が最後に回る。これは答えではなく範囲の指示なので、
# 「多くの問に共通して現れる識別子は質問の定型」とみなして外す（PE3c）。
#
# 閾値は問題集合から機械的に決まるので事前登録できる。BOILERPLATE_MIN_DOCS
# 問以上に現れる識別子を定型とする。現在の 284 問では定型の 20 語が 35 問
# ちょうどに現れ、本物の記号で最も多いものは 5 問（`Repo`）なので、6 以上
# 35 以下のどの閾値でも同じ 20 語が選ばれる（`l1/tasks.py df` が分布を出す）。
BOILERPLATE_MIN_DOCS = int(os.environ.get("ARE_L1_BOILERPLATE_MIN_DOCS", "10"))
# PE3b の測定（規則を入れる前）を再現するための逃げ道。既定は外す。
DROP_BOILERPLATE = os.environ.get("ARE_L1_DROP_BOILERPLATE", "1") == "1"


def identifiers(question):
    """The identifiers of a question, in order, without repeats."""
    out = []
    for span in BACKTICK.findall(question):
        if "/" in span:
            continue
        for tok in IDENT.findall(span):
            if len(tok) >= 3 and tok.lower() not in KEYWORDS \
                    and tok.lower() not in CORPUS_WORDS and tok not in out:
                out.append(tok)
    plain = BACKTICK.sub(" ", question)
    for tok in SNAKE_OR_CAMEL.findall(plain):
        if len(tok) >= 4 and tok.lower() not in KEYWORDS \
                and tok.lower() not in CORPUS_WORDS and tok not in out:
            out.append(tok)
    return out


def terms_ja(question, drop=()):
    out = [x for x in identifiers(question) if x not in drop]
    plain = BACKTICK.sub(" ", question)
    for tok in JA_RUN.findall(plain):
        if tok not in JA_STOP and tok not in out:
            out.append(tok)
    return out[:MAX_TERMS]


def terms_en(question_en, drop=()):
    out = [x for x in identifiers(question_en) if x not in drop]
    plain = BACKTICK.sub(" ", question_en)
    for tok in EN_WORD.findall(plain):
        if tok.lower() in EN_STOP or len(tok) < 3:
            continue
        if tok.lower() in CORPUS_WORDS:
            continue
        if tok not in out and tok.lower() not in [o.lower() for o in out]:
            out.append(tok)
    return out[:MAX_TERMS]


def forms(task, drop=()):
    """The three query forms of a task, per language."""
    q, qe = task["question"], task["question_en"]
    keep = lambda xs: [x for x in xs if x not in drop]  # noqa: E731
    return {
        "ident": keep(identifiers(q) or identifiers(qe)),
        "terms_ja": terms_ja(q, drop),
        "terms_en": terms_en(qe, drop),
        "nat_ja": unicodedata.normalize("NFKC", q),
        "nat_en": qe,
    }


def _listdir_files(root, rel):
    """The files under a directory of the corpus, relative to the corpus."""
    base = os.path.join(root, rel)
    out = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for f in filenames:
            out.append(os.path.relpath(os.path.join(dirpath, f), root))
    return sorted(out)


def qrels(task):
    """The relevant files of a task, as <repo>/<path>, all of grade 1."""
    root = os.path.join(CORPORA, task["corpus"])
    rel = []
    for e in task["evidence"]:
        if e.endswith("/"):
            rel += _listdir_files(root, e)
        else:
            rel.append(e)
    # The gold of S1/S2/S9 also names files; keep them in the set.
    g = task["gold"]
    repo = g.get("repo")
    if repo and g["type"] in ("path", "files"):
        vals = g["value"] if isinstance(g["value"], list) else [g["value"]]
        rel += [f"{repo}/{v}" for v in vals]
    if g["type"] == "rubric":
        for r in g.get("ref", []):
            rel.append(f"{r['repo']}/{r['file']}")
    return sorted(set(rel))


def doc_freq(tasks):
    """識別子ごとの「その識別子が現れる問の数」。定型の判定の材料。"""
    return collections.Counter(tok for t in tasks for tok in set(t["forms"]["ident"]))


def boilerplate(tasks, min_docs=BOILERPLATE_MIN_DOCS):
    """質問の定型とみなす識別子の集合。

    問題集合だけから機械的に決まる（道具も測定結果も使わない）ので、
    事前登録できる。min_docs 問以上に現れる識別子を定型とする。
    """
    return {tok for tok, k in doc_freq(tasks).items() if k >= min_docs}


def load(path=None, scenarios=L1_SCENARIOS):
    """L1 の問。定型の判定は **L1 の全問**で行う（--scenarios で絞っても同じ）。"""
    path = path or TASKS
    all_l1 = []
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            t = json.loads(line)
            if t["scenario"] not in L1_SCENARIOS:
                continue
            t["forms"] = forms(t)
            all_l1.append(t)
    drop = boilerplate(all_l1) if DROP_BOILERPLATE else set()
    out = []
    for t in all_l1:
        if drop:
            t["forms"] = forms(t, drop)
        if t["scenario"] not in scenarios:
            continue
        t["qrels"] = qrels(t)
        out.append(t)
    return out


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def provenance(path=None):
    """記録に添える、問題ファイルの版と識別子の取り出しの規則。

    PE3b の記録には問題ファイルの版が入っておらず、前回の測定を採点し直す
    ことができなかった（pe3b-l1.md 第 3 節）。以後はこれを毎回の記録に持たせる。
    """
    path = path or TASKS
    all_l1 = []
    with open(path) as f:
        for line in f:
            if line.strip():
                t = json.loads(line)
                if t["scenario"] in L1_SCENARIOS:
                    t["forms"] = forms(t)
                    all_l1.append(t)
    drop = sorted(boilerplate(all_l1)) if DROP_BOILERPLATE else []
    return {"file": os.path.relpath(path, ROOT), "sha256": sha256(path),
            "n_l1": len(all_l1),
            "ident_rule": {"drop_boilerplate": DROP_BOILERPLATE,
                           "min_docs": BOILERPLATE_MIN_DOCS if DROP_BOILERPLATE else None,
                           "dropped": drop}}


def _df_report():
    """定型の閾値の根拠: 識別子が現れる問の数の分布と、閾値を動かした結果。"""
    import sys
    all_l1 = []
    with open(TASKS) as f:
        for line in f:
            if line.strip():
                t = json.loads(line)
                if t["scenario"] in L1_SCENARIOS:
                    t["forms"] = forms(t)
                    all_l1.append(t)
    n = doc_freq(all_l1)
    print(f"L1 {len(all_l1)} 問、異なり識別子 {len(n)} 語\n")
    print("| 現れる問の数 | 識別子の数 | 識別子 |")
    print("|---|--:|---|")
    for k in sorted(set(n.values()), reverse=True):
        w = sorted(t for t, v in n.items() if v == k)
        print(f"| {k} | {len(w)} | " + "・".join(f"`{x}`" for x in (w if len(w) <= 20 else w[:8] + ["…"])) + " |")
    print()
    print("| 閾値（この問数以上で定型） | 外れる語 | 識別子が変わる問 |")
    print("|---:|--:|--:|")
    for m in (2, 3, 4, 5, 6, 10, 20, 35, 36):
        drop = {t for t, v in n.items() if v >= m}
        ch = sum(1 for t in all_l1 if forms(t, drop)["ident"] != t["forms"]["ident"])
        print(f"| {m} | {len(drop)} | {ch} |")
    print(f"\n既定の閾値 {BOILERPLATE_MIN_DOCS}: 外す語 "
          + "・".join(f"`{x}`" for x in sorted(boilerplate(all_l1))), file=sys.stdout)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "df":
        _df_report()
        raise SystemExit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "provenance":
        print(json.dumps(provenance(), ensure_ascii=False, indent=1))
        raise SystemExit(0)
    ts = load()
    print(f"{len(ts)} tasks for L1", file=sys.stderr)
    n_empty = collections.Counter()
    sizes = collections.Counter()
    for t in ts:
        if not t["forms"]["ident"]:
            n_empty[(t["scenario"], t["phrasing"])] += 1
        sizes[t["scenario"]] += len(t["qrels"])
    print("questions without an identifier:", dict(n_empty), file=sys.stderr)
    for t in ts[:8] + ts[-4:]:
        print(json.dumps({"id": t["id"], "forms": t["forms"], "qrels": t["qrels"][:5],
                          "n_qrels": len(t["qrels"])}, ensure_ascii=False))
