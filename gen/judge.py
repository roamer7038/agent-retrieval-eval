#!/usr/bin/env python3
"""A prototype of the LLM judge for free-text answers (S4 design reasons, S5
procedures), and the answers it was tried on.

    gen/judge.py answers [--split dev] [--n 20]   # answers to judge (ANSWER_MODEL)
    gen/judge.py judge [--split dev]              # two judges' verdicts
    gen/judge.py agree <human.jsonl>              # agreement with a person's labels
    gen/judge.py s9check [--split dev]            # S9: does each gold file handle the term
    gen/judge.py s9apply [--split dev]            # S9: keep the files the judge calls handled
    gen/judge.py s9agree <human.jsonl>            # agreement of s9check with a person

The answers are made to vary in quality: one written from the gold section,
one from the gold section but in a single sentence (so that it may leave out
key points: the hard cases), one written from another candidate's section,
one written from no text. The judge sees the question,
the key points of the gold, the gold section and the answer, and decides per
key point whether the answer states it, and overall "correct" (every key
point, nothing that contradicts the section) or "incorrect". Two judges of different lineage (gemma4 and muse-glimmer) judge every
answer. They agree on most, and the ones they split on are written down so
that a person can settle those alone. A person labels the same answers blind
to the judges (human.jsonl: {"answer_id", "correct"}), and agree reports the
raw agreement and Cohen's kappa of each judge, of the two judges with each
other, and of their agreed verdicts.
S9 answers are lists of files and are graded without a judge; what needs a
judgement there is the gold: a file that holds the term only in a list of
names is not the file that handles it. s9check shows the judge (one here,
S9_JUDGES) the document's section and the lines that tie the file to the term,
and asks whether the file handles (or, for a page, explains) the term;
"handles" is read widely (the value is read, written, decides what the code
does, or reaches the file through a function that returns it). s9apply then
keeps in the gold the files the judge calls handled and moves the rest to
"optional", so that a file which only lists the name is not required. The
agent settles the ones it disagrees with in results/pe2-s9-decisions.jsonl.
Files: gold-work/judge/<split>/answers.jsonl, verdicts.jsonl, s9check.jsonl
"""
import argparse
import collections
import json
import os
import sys

import llm
from common import ROOT, WORK, read_jsonl, read_lines, rng, write_jsonl

ANSWER_MODEL = "ornith-1.5:35b"
JUDGE_MODEL = llm.CHECK_MODEL
# two judges of different lineage; the answers are written by a third model
JUDGES = [("A", llm.CHECK_MODEL), ("B", llm.CHECK_MODEL_2)]
# The gold of S9 is picked by one judge and the agent's decision. The second
# judge was measured on this task in PE2b and could not do it (agreement
# 0.793, kappa about 0), while it was the better of the two on the free-text
# answers, so it stays there and is left out here (the decision of 2026-09-20).
S9_JUDGES = JUDGES[:1]
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
    """Both judges on every answer. "verdict" is what they agree on and None
    when they split; "split" says which those are, for a person to settle."""
    rows = read_jsonl(os.path.join(JDIR, split, "answers.jsonl"))
    qs = {q["base_id"]: q for q in questions(split)}
    out, splits = [], 0
    for a in rows:
        q = qs[a["base_id"]]
        prompt = JUDGE.format(q=a["question"], points="\n".join(f"{i + 1}. {p}" for i, p in enumerate(a["points"])),
                              ref=ref_text(q), answer=a["answer"])
        by, res = {}, {}
        for name, model in JUDGES:
            j = llm.chat_json(model, prompt, schema=JUDGE_SCHEMA)
            by[name] = (j or {}).get("verdict")
            res[name] = j
        vs = set(by.values())
        agreed = len(vs) == 1 and None not in vs
        splits += not agreed
        out.append({"answer_id": a["answer_id"], "verdict": by["A"] if agreed else None, "split": not agreed,
                    "by": by, "models": {n: m for n, m in JUDGES}, "result": res})
    write_jsonl(os.path.join(JDIR, split, "verdicts.jsonl"), out)
    print(len(out), "verdicts,", splits, "split", llm.stats, file=sys.stderr)


S9CHECK = """文書とソースコードの対応を確認してください。

文書の記述（{term} について）:
{doc}

次は、ファイル {file} の中で {look} が現れる箇所です（前後の行を含む）:
{snippets}

このファイルは `{term}` を「扱っている」と言えるかを判定してください。「扱っている」は広くとります。`{term}` の値は、設定を読む箇所で構造体のフィールドに入り、そのフィールドを返す短い関数を通して他のファイルへ渡ることがあります。そのため、ファイルが `{term}` という文字列そのものを書いていなくても、その値を受け取って使っていれば「扱っている」に当たります（上の抜粋は、そのフィールドや関数が現れる箇所です）。
- コードのファイルなら: `{term}` の値を読み取る・設定する・既定値や許される値を定める・他へ渡す・その値によって処理を変えるなら "handles"。値を返す関数の戻り値を使って処理を変えるのも "handles"。名前が一覧・表・コメント・テストのデータに現れるだけで、その値に応じて何かが変わる記述がどこにも無いなら "names_only"。
- 文書のページなら: `{term}` が何であるか・どう使うか・どう効くかを説明していれば "handles"。一覧や例の中に名前が現れるだけなら "names_only"。
- reason に理由を短く書く。
JSON で答えてください。"""

S9_SCHEMA = {"type": "object", "properties": {"role": {"type": "string", "enum": ["handles", "names_only"]},
                                              "reason": {"type": "string"}}, "required": ["role", "reason"]}


def snippets(corpus, repo, path, terms, around=4, most=3):
    """The lines that tie a file to the term. A file can be in the gold
    without writing the key: it reads the struct field the value is kept in,
    or it calls the function that returns it. The words to look for therefore
    come from the candidate (source.gold_terms), not from the key alone;
    before PE2c this looked for the key only and showed the judges nothing at
    all for the files that hold the value."""
    from common import repo_dir
    if isinstance(terms, str):
        terms = [terms]
    terms = [t for t in terms if t]
    try:
        lines = open(os.path.join(repo_dir(corpus, repo), path), errors="replace").read().split("\n")
    except OSError:
        return ""
    hits = [i for i, l in enumerate(lines) if any(t in l for t in terms)][:most]
    out = []
    for i in hits:
        a, b = max(0, i - around), min(len(lines), i + around + 1)
        out.append(f"--- {a + 1}〜{b} 行\n" + "\n".join(lines[a:b]))
    return "\n".join(out)[:2500]


def s9check(split):
    from common import CAND
    rows = read_jsonl(os.path.join(CAND, split, "S9.jsonl"))
    out, splits = [], 0
    for c in rows:
        term, g = c["subject"]["label"], c["gold"]
        gterms = (c.get("source") or {}).get("gold_terms") or {}
        for f in g["value"]:   # only the files the gold requires
            look = sorted(set(gterms.get(f) or [term]))
            snip = snippets(c["corpus"], g["repo"], f, look)
            prompt = S9CHECK.format(term=term, doc=c["subject"]["doc"][:800], file=f, snippets=snip,
                                    look="・".join(f"`{x}`" for x in look))
            by, res = {}, {}
            for name, model in S9_JUDGES:
                j = llm.chat_json(model, prompt, schema=S9_SCHEMA)
                by[name] = (j or {}).get("role")
                res[name] = j
            agreed = len(set(by.values())) == 1 and None not in set(by.values())
            splits += not agreed
            out.append({"item": f"{c['base_id']}:{f}", "base_id": c["base_id"], "file": f,
                        "role": by["A"] if agreed else None, "split": not agreed, "by": by, "result": res})
    write_jsonl(os.path.join(JDIR, split, "s9check.jsonl"), out)
    print(len(out), "files,", splits, "split", llm.stats, file=sys.stderr)


# A person's (here: the agent's) decision on the files the two judges split
# on, one line of {"item", "role", "note"}; s9apply follows it.
S9_DECISIONS = os.path.join(ROOT, "results", "pe2-s9-decisions.jsonl")


def s9apply(split):
    """Rewrite the S9 candidates. A file both judges call a list of names
    moves to "optional", where naming it is neither required nor wrong; the
    files they split on stay in the gold unless a decision in
    results/pe2-s9-decisions.jsonl says otherwise, and are printed so that
    they can be settled. A candidate with no file left is dropped."""
    from common import CAND
    path = os.path.join(CAND, split, "S9.jsonl")
    checks = collections.defaultdict(dict)
    for x in read_jsonl(os.path.join(JDIR, split, "s9check.jsonl")):
        checks[x["base_id"]][x["file"]] = x
    decided = {}
    if os.path.exists(S9_DECISIONS):
        decided = {d["item"]: d["role"] for d in read_jsonl(S9_DECISIONS)}
    rows, dropped, moved, undecided = [], [], 0, []

    def role(base_id, f):
        item = f"{base_id}:{f}"
        if item in decided:
            return decided[item]
        x = checks[base_id].get(f) or {}
        if x.get("split"):
            undecided.append(item)
            return "handles"   # kept until a person settles it
        return x.get("role")

    for c in read_jsonl(path):
        g = c["gold"]
        files = list(g["value"]) + list(g.get("optional") or [])
        keep = [f for f in files if role(c["base_id"], f) == "handles"]
        rest = [f for f in files if f not in keep]
        moved += len(rest)
        if not keep:
            dropped.append(c["base_id"])
            continue
        g["value"], g["optional"] = sorted(keep), sorted(rest)
        c["s9check"] = {f: (checks[c["base_id"]].get(f, {}) or {}).get("by") for f in files}
        rows.append(c)
    write_jsonl(path, rows)
    print(json.dumps({"kept": len(rows), "dropped": dropped, "files_moved_to_optional": moved,
                      "undecided_splits": undecided}, ensure_ascii=False), file=sys.stderr)


def s9agree(split, human_path):
    rows = {x["item"]: x for x in read_jsonl(os.path.join(JDIR, split, "s9check.jsonl"))}
    h = {x["item"]: x["role"] == "handles" for x in read_jsonl(human_path)}
    ids = sorted(set(rows) & set(h))
    out = {"n": len(ids)}
    for name, _ in S9_JUDGES:
        pairs = [(h[i], rows[i]["by"].get(name) == "handles") for i in ids if rows[i]["by"].get(name)]
        po, k = kappa(pairs) if pairs else (float("nan"), float("nan"))
        out[f"judge_{name}"] = {"n": len(pairs), "agreement": round(po, 3), "kappa": round(k, 3),
                                "disagreements": [i for i in ids
                                                  if rows[i]["by"].get(name) and h[i] != (rows[i]["by"][name] == "handles")]}
    both = [i for i in ids if rows[i]["by"].get("A") and rows[i]["by"].get("B")]
    if both:
        jj = [(rows[i]["by"]["A"] == "handles", rows[i]["by"]["B"] == "handles") for i in both]
        po, k = kappa(jj)
        out["judge_A_vs_B"] = {"n": len(jj), "agreement": round(po, 3), "kappa": round(k, 3),
                               "split_items": [i for i in both if rows[i]["by"]["A"] != rows[i]["by"]["B"]]}
    print(json.dumps(out, ensure_ascii=False, indent=1))


def kappa(pairs):
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    pa = sum(a for a, _ in pairs) / n
    pb = sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return po, (po - pe) / (1 - pe) if pe < 1 else float("nan")


def agree(split, human_path):
    """Each judge against the person's labels, the two judges against each
    other, and the answers they agree on against the person."""
    rows = {x["answer_id"]: x for x in read_jsonl(os.path.join(JDIR, split, "verdicts.jsonl"))}
    h = {x["answer_id"]: bool(x["correct"]) for x in read_jsonl(human_path)}
    ids = sorted(set(rows) & set(h))
    out = {"n": len(ids)}
    for name, _ in JUDGES:
        pairs = [(h[i], rows[i]["by"].get(name) == "correct") for i in ids
                 if rows[i]["by"].get(name) is not None]
        po, k = kappa(pairs)
        t = collections.Counter(pairs)
        out[f"judge_{name}"] = {"n": len(pairs), "agreement": round(po, 3), "kappa": round(k, 3),
                                "human_correct_llm_incorrect": t[(True, False)],
                                "human_incorrect_llm_correct": t[(False, True)]}
    both = [i for i in ids if rows[i]["by"].get("A") is not None and rows[i]["by"].get("B") is not None]
    jj = [(rows[i]["by"]["A"] == "correct", rows[i]["by"]["B"] == "correct") for i in both]
    po, k = kappa(jj) if jj else (float("nan"), float("nan"))
    out["judge_A_vs_B"] = {"n": len(jj), "agreement": round(po, 3), "kappa": round(k, 3)}
    agreed = [i for i in both if rows[i]["by"]["A"] == rows[i]["by"]["B"]]
    pairs = [(h[i], rows[i]["by"]["A"] == "correct") for i in agreed]
    po, k = kappa(pairs) if pairs else (float("nan"), float("nan"))
    out["agreed_only_vs_human"] = {"n": len(pairs), "agreement": round(po, 3), "kappa": round(k, 3)}
    out["split_answers"] = [i for i in both if rows[i]["by"]["A"] != rows[i]["by"]["B"]]
    print(json.dumps(out, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["answers", "judge", "agree", "s9check", "s9apply", "s9agree"])
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
    elif a.cmd == "s9apply":
        s9apply(a.split)
    else:
        s9agree(a.split, a.human)


if __name__ == "__main__":
    main()
