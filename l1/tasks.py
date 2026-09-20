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
import json
import os
import re
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("ARE_DATA", os.path.join(ROOT, "data"))
CORPORA = os.path.join(DATA, "corpora")

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


def terms_ja(question):
    out = list(identifiers(question))
    plain = BACKTICK.sub(" ", question)
    for tok in JA_RUN.findall(plain):
        if tok not in JA_STOP and tok not in out:
            out.append(tok)
    return out[:MAX_TERMS]


def terms_en(question_en):
    out = list(identifiers(question_en))
    plain = BACKTICK.sub(" ", question_en)
    for tok in EN_WORD.findall(plain):
        if tok.lower() in EN_STOP or len(tok) < 3:
            continue
        if tok.lower() in CORPUS_WORDS:
            continue
        if tok not in out and tok.lower() not in [o.lower() for o in out]:
            out.append(tok)
    return out[:MAX_TERMS]


def forms(task):
    """The three query forms of a task, per language."""
    q, qe = task["question"], task["question_en"]
    return {
        "ident": identifiers(q) or identifiers(qe),
        "terms_ja": terms_ja(q),
        "terms_en": terms_en(qe),
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


def load(path=None, scenarios=L1_SCENARIOS):
    path = path or os.path.join(ROOT, "tasks", "dev", "pe2.jsonl")
    out = []
    with open(path) as f:
        for line in f:
            t = json.loads(line)
            if t["scenario"] not in scenarios:
                continue
            t["forms"] = forms(t)
            t["qrels"] = qrels(t)
            out.append(t)
    return out


if __name__ == "__main__":
    import collections
    import sys
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
