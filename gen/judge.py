#!/usr/bin/env python3
"""A prototype of the LLM judge for free-text answers (S4 design reasons, S5
procedures), and the answers it was tried on.

    gen/judge.py answers [--split dev] [--n 20]   # answers to judge (ANSWER_MODEL)
    gen/judge.py judge [--split dev]              # the judge's verdicts (JUDGE_MODEL)
    gen/judge.py agree <human.jsonl>              # agreement with a person's labels
    gen/judge.py s9check [--split dev]            # S9: does each gold file handle the term
    gen/judge.py s9agree <human.jsonl>            # agreement of s9check with a person

The answers are made to vary in quality: one written from the gold section,
one from the gold section but in a single sentence (so that it may leave out
key points: the hard cases), one written from another candidate's section,
one written from no text. The judge sees the question,
the key points of the gold, the gold section and the answer, and decides per
key point whether the answer states it, and overall "correct" (every key
point, nothing that contradicts the section) or "incorrect". A person labels
the same answers blind to the judge (human.jsonl: {"answer_id", "correct"}),
and agree reports the raw agreement and Cohen's kappa.
S9 answers are lists of files and are graded without a judge; what needs a
judgement there is the gold: a file that holds the term only in a list of
names is not the file that handles it. s9check shows the judge the
document's section and the lines around the term in each gold file, and asks
whether the file handles (or, for a page, explains) the term.
Files: gold-work/judge/<split>/answers.jsonl, verdicts.jsonl, s9check.jsonl
"""
import argparse
import collections
import json
import os
import sys

import llm
from common import WORK, read_jsonl, read_lines, rng, write_jsonl

ANSWER_MODEL = "ornith-1.5:35b"
JUDGE_MODEL = llm.CHECK_MODEL
JDIR = os.path.join(WORK, "judge")

ANSWER = """次の質問に、日本語で答えてください。{ctx}

質問: {q}
答えの形: {fmt}

答えだけを書いてください。"""

JUDGE = """あなたは採点者です。質問に対する回答が正しいかを、正解の要点と、正解の根拠の文書に照らして判定してください。

質問: {q}

正解の要点:
{points}

正解の根拠の文書（抜粋）:
{ref}

回答:
{answer}

判定の規則:
- 要点ごとに、回答がその内容を述べているか（言い方が違っても意味が同じなら述べている）を points に true / false で書く。
- 要点に含まれる具体（コマンド、設定やフィールドの名前、パス、値、固有名）を欠いた一般論は、その要点を述べたことにしない（false）。
- 根拠の文書に書かれていないことを回答が正しいと仮定して補わない。文書に無い一般論だけの回答は、要点を述べていない。
- 回答に、根拠の文書と矛盾する内容があれば contradiction を true にする。
- すべての要点を述べていて、矛盾が無ければ verdict を "correct"、そうでなければ "incorrect" にする。
- 要点に無い内容を足していても、根拠と矛盾しなければ減点しない。
- reason に判断の理由を短く書く。
JSON で答えてください。"""

JUDGE_SCHEMA = {"type": "object", "properties": {
    "points": {"type": "array", "items": {"type": "boolean"}}, "contradiction": {"type": "boolean"},
    "verdict": {"type": "string", "enum": ["correct", "incorrect"]}, "reason": {"type": "string"}},
    "required": ["points", "contradiction", "verdict", "reason"]}


def ref_text(q):
    r = q["gold"]["ref"][0]
    corpus = q["corpus"]
    return read_lines(corpus, r["repo"], r["file"], r["lines"][0], r["lines"][1])[:3000]


def questions(split):
    from build import audits
    bad = {b for b, a in audits().items() if a["verdict"] in ("error", "invalid")}
    out = []
    for s in ("S4", "S5"):
        p = os.path.join(WORK, "phrased", split, f"{s}.jsonl")
        if os.path.exists(p):
            out += [q for q in read_jsonl(p) if q.get("status") == "phrased" and q["gold"].get("points")
                    and q["base_id"] not in bad]
    return out


def make_answers(split, n):
    qs = questions(split)
    r = rng(split, "judge", "answers")
    by = collections.defaultdict(list)
    for q in qs:
        by[(q["scenario"], q["corpus"])].append(q)
    pick = []
    for k in sorted(by):
        r.shuffle(by[k])
    # round-robin over scenario x corpus
    while len(pick) < n and any(by.values()):
        for k in sorted(by):
            if by[k] and len(pick) < n:
                pick.append(by[k].pop())
    rows = []
    for q in pick:
        others = [o for o in qs if o is not q and o["corpus"] == q["corpus"]]
        other = r.choice(others) if others else None
        for kind in ("gold", "brief", "other", "none"):
            fmt = q["answer_format"]
            if kind in ("gold", "brief"):
                ctx = "\n\n参考の文書:\n" + ref_text(q)
                if kind == "brief":
                    fmt = "1 文だけで、要点を 1 つに絞って"
            elif kind == "other" and other:
                ctx = "\n\n参考の文書:\n" + ref_text(other)
            else:
                ctx = ""
            a = llm.chat(ANSWER_MODEL, ANSWER.format(ctx=ctx, q=q["q_para"], fmt=fmt))
            rows.append({"answer_id": f"{q['base_id']}:{kind}", "base_id": q["base_id"], "scenario": q["scenario"],
                         "corpus": q["corpus"], "kind": kind, "question": q["q_para"], "points": q["gold"]["points"],
                         "ref": q["gold"]["ref"][0], "answer": a.strip()})
    write_jsonl(os.path.join(JDIR, split, "answers.jsonl"), rows)
    print(len(rows), "answers", llm.stats, file=sys.stderr)


def judge(split):
    rows = read_jsonl(os.path.join(JDIR, split, "answers.jsonl"))
    qs = {q["base_id"]: q for q in questions(split)}
    out = []
    for a in rows:
        q = qs[a["base_id"]]
        j = llm.chat_json(JUDGE_MODEL, JUDGE.format(q=a["question"], points="\n".join(f"{i + 1}. {p}" for i, p in enumerate(a["points"])),
                                                    ref=ref_text(q), answer=a["answer"]), schema=JUDGE_SCHEMA)
        out.append({"answer_id": a["answer_id"], "verdict": (j or {}).get("verdict"), "result": j})
    write_jsonl(os.path.join(JDIR, split, "verdicts.jsonl"), out)
    print(len(out), "verdicts", llm.stats, file=sys.stderr)


S9CHECK = """文書とソースコードの対応を確認してください。

文書の記述（{term} について）:
{doc}

次は、ファイル {file} の中で `{term}` が現れる箇所です（前後の行を含む）:
{snippets}

このファイルは `{term}` を「扱っている」と言えるかを判定してください。
- コードのファイルなら: `{term}` の値を読み取る・設定する・その値に従って処理を変えるなら "handles"。名前の一覧・表・コメント・テストのデータに名前が現れるだけなら "names_only"。
- 文書のページなら: `{term}` が何であるか・どう使うかを説明していれば "handles"。一覧や例の中に名前が現れるだけなら "names_only"。
- reason に理由を短く書く。
JSON で答えてください。"""

S9_SCHEMA = {"type": "object", "properties": {"role": {"type": "string", "enum": ["handles", "names_only"]},
                                              "reason": {"type": "string"}}, "required": ["role", "reason"]}


def snippets(corpus, repo, path, term, around=4, most=3):
    from common import repo_dir
    try:
        lines = open(os.path.join(repo_dir(corpus, repo), path), errors="replace").read().split("\n")
    except OSError:
        return ""
    hits = [i for i, l in enumerate(lines) if term in l][:most]
    out = []
    for i in hits:
        a, b = max(0, i - around), min(len(lines), i + around + 1)
        out.append(f"--- {a + 1}〜{b} 行\n" + "\n".join(lines[a:b]))
    return "\n".join(out)[:2500]


def s9check(split):
    from common import CAND
    rows = read_jsonl(os.path.join(CAND, split, "S9.jsonl"))
    out = []
    for c in rows:
        term, g = c["subject"]["label"], c["gold"]
        for f in g["value"]:
            snip = snippets(c["corpus"], g["repo"], f, term)
            j = llm.chat_json(JUDGE_MODEL, S9CHECK.format(term=term, doc=c["subject"]["doc"][:800], file=f, snippets=snip),
                              schema=S9_SCHEMA)
            out.append({"item": f"{c['base_id']}:{f}", "base_id": c["base_id"], "role": (j or {}).get("role"), "result": j})
    write_jsonl(os.path.join(JDIR, split, "s9check.jsonl"), out)
    print(len(out), "files", llm.stats, file=sys.stderr)


def s9agree(split, human_path):
    v = {x["item"]: x["role"] == "handles" for x in read_jsonl(os.path.join(JDIR, split, "s9check.jsonl"))}
    h = {x["item"]: x["role"] == "handles" for x in read_jsonl(human_path)}
    ids = sorted(set(v) & set(h))
    pairs = [(h[i], v[i]) for i in ids]
    po, k = kappa(pairs)
    print(json.dumps({"n": len(pairs), "agreement": round(po, 3), "kappa": round(k, 3),
                      "disagreements": [i for i in ids if h[i] != v[i]]}, ensure_ascii=False, indent=1))


def kappa(pairs):
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    pa = sum(a for a, _ in pairs) / n
    pb = sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return po, (po - pe) / (1 - pe) if pe < 1 else float("nan")


def agree(split, human_path):
    v = {x["answer_id"]: x["verdict"] == "correct" for x in read_jsonl(os.path.join(JDIR, split, "verdicts.jsonl"))}
    h = {x["answer_id"]: bool(x["correct"]) for x in read_jsonl(human_path)}
    ids = sorted(set(v) & set(h))
    pairs = [(h[i], v[i]) for i in ids]
    po, k = kappa(pairs)
    table = collections.Counter((a, b) for a, b in pairs)
    print(json.dumps({"n": len(pairs), "agreement": round(po, 3), "kappa": round(k, 3),
                      "human_correct_llm_correct": table[(True, True)], "human_correct_llm_incorrect": table[(True, False)],
                      "human_incorrect_llm_correct": table[(False, True)],
                      "human_incorrect_llm_incorrect": table[(False, False)],
                      "disagreements": [i for i in ids if h[i] != v[i]]}, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["answers", "judge", "agree", "s9check", "s9agree"])
    ap.add_argument("human", nargs="?")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--n", type=int, default=20)
    a = ap.parse_args()
    if a.cmd == "answers":
        make_answers(a.split, a.n)
    elif a.cmd == "judge":
        judge(a.split)
    elif a.cmd == "agree":
        agree(a.split, a.human)
    elif a.cmd == "s9check":
        s9check(a.split)
    else:
        s9agree(a.split, a.human)


if __name__ == "__main__":
    main()
