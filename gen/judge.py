#!/usr/bin/env python3
"""A prototype of the LLM judge for free-text answers (S4 design reasons, S5
procedures), and the answers it was tried on.

    gen/judge.py answers [--split dev] [--n 20]   # answers to judge (ANSWER_MODEL)
    gen/judge.py judge [--split dev]              # the judge's verdicts (JUDGE_MODEL)
    gen/judge.py agree <human.jsonl>              # agreement with a person's labels

The answers are made to vary in quality: one written from the gold section,
one written from a neighbouring section of the same page (or another
candidate's section), one written from no text. The judge sees the question,
the key points of the gold, the gold section and the answer, and decides per
key point whether the answer states it, and overall "correct" (every key
point, nothing that contradicts the section) or "incorrect". A person labels
the same answers blind to the judge (human.jsonl: {"answer_id", "correct"}),
and agree reports the raw agreement and Cohen's kappa.
Files: gold-work/judge/<split>/answers.jsonl, verdicts.jsonl
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
    out = []
    for s in ("S4", "S5"):
        p = os.path.join(WORK, "phrased", split, f"{s}.jsonl")
        if os.path.exists(p):
            out += [q for q in read_jsonl(p) if q.get("status") == "phrased" and q["gold"].get("points")]
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
        for kind in ("gold", "other", "none"):
            if kind == "gold":
                ctx = "\n\n参考の文書:\n" + ref_text(q)
            elif kind == "other" and other:
                ctx = "\n\n参考の文書:\n" + ref_text(other)
            else:
                ctx = ""
            a = llm.chat(ANSWER_MODEL, ANSWER.format(ctx=ctx, q=q["q_para"], fmt=q["answer_format"]))
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
    ap.add_argument("cmd", choices=["answers", "judge", "agree"])
    ap.add_argument("human", nargs="?")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--n", type=int, default=20)
    a = ap.parse_args()
    if a.cmd == "answers":
        make_answers(a.split, a.n)
    elif a.cmd == "judge":
        judge(a.split)
    else:
        agree(a.split, a.human)


if __name__ == "__main__":
    main()
