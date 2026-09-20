#!/usr/bin/env python3
"""Questions from the candidates, with the local LLMs: the question with
identifiers, the paraphrase without them, a check that the paraphrase still
points at one answer, and the English translation with its check.

    gen/phrase.py [--split dev] [--scenario S1,...] [--limit N]

Stages, run model by model so the server does not switch models per item
(the server is called one request at a time):
  1. qgen      (GEN_MODEL)   S2, S4, S5: the question with identifiers and
                             the key points, from the commit message or the
                             section (the only step that sees corpus text)
  2. para      (GEN_MODEL)   the paraphrase, from the question, the list of
                             identifiers to avoid and a short description of
                             the subject (a doc comment, a page title); no files
  3. judge     (CHECK_MODEL) multiple choice: which of the subject and its
                             neighbours (other functions of the package, other
                             sections of the page, ...) does the paraphrase ask
                             about; kept only when it picks the subject. With
                             fewer than three neighbours: does the paraphrase
                             ask the same thing as the question with identifiers
  4. retry     (GEN_MODEL, CHECK_MODEL) one more paraphrase for the rejected,
                             told why, and the judge again
  5. translate (CHECK_MODEL) both questions and the answer format to English
  6. tcheck    (GEN_MODEL)   the Japanese and English ask the same thing, with
                             the identifiers unchanged
Output: gold-work/phrased/<split>/<scenario>.jsonl and stats.json
"""
import argparse
import collections
import glob
import json
import os
import re
import sys

import llm
from common import CAND, WORK, read_jsonl, rng, write_jsonl

PHRASED = os.path.join(WORK, "phrased")

QGEN_SCHEMA = {"type": "object", "properties": {
    "question": {"type": "string"}, "identifiers": {"type": "array", "items": {"type": "string"}},
    "key_points": {"type": "array", "items": {"type": "string"}}, "answerable": {"type": "boolean"}},
    "required": ["question", "identifiers", "key_points", "answerable"]}
PARA_SCHEMA = {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}
JUDGE_SCHEMA = {"type": "object", "properties": {"choice": {"type": "string"}, "ambiguous": {"type": "boolean"},
                                                 "reason": {"type": "string"}},
                "required": ["choice", "ambiguous", "reason"]}
EQ_SCHEMA = {"type": "object", "properties": {"same": {"type": "boolean"}, "reason": {"type": "string"}},
             "required": ["same", "reason"]}
TR_SCHEMA = {"type": "object", "properties": {"question_identifier": {"type": "string"},
                                              "question_paraphrase": {"type": "string"},
                                              "answer_format": {"type": "string"}},
             "required": ["question_identifier", "question_paraphrase", "answer_format"]}
TC_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}, "problem": {"type": "string"}},
             "required": ["ok", "problem"]}


# ---------------------------------------------------------------- stage 1

QGEN = {
    "S2": """次は、あるソフトウェア（{repo}）に取り込まれた変更のコミットメッセージです。

{text}

この変更で入った（または直った）「振る舞い」が、現在のソースコードのどこに実装されているかを尋ねる日本語の質問を 1 つ作ってください。
- 質問は変更の履歴を尋ねるのではなく、現在の振る舞い（〜するとき〜する処理）の実装箇所を尋ねる形にする。「この変更」「このコミット」とは書かない。
- 質問の中に、次の識別子のうち 1 つ以上をバッククォートで囲んでそのまま含める: {idents}（候補が無ければ、コミットメッセージに出てくるコードの名前を 1 つ含める）
- 質問の文末は「〜は、どのファイルで実装されているか。」とする。
- identifiers には、質問に含めた識別子・コードの名前・パスをすべて入れる。
- key_points は空の配列でよい。answerable は、コミットメッセージから振る舞いが読み取れれば true。
JSON で答えてください。""",
    "S4": """次は、{repo} の文書（またはソースコードのコメント）の一節です（見出し: {heading}）。

{text}

この一節に書かれている「設計や方針の理由」を尋ねる日本語の質問を 1 つと、その答えの要点を作ってください。
- 質問は「なぜ〜なのか」の形にし、この一節だけから答えられるものにする。
- 質問には、一節に出てくる固有の語（コードの名前、設定の名前、製品・機能の名前など）を 1 つ以上、そのまま含める。コードの名前はバッククォートで囲む。
- **質問に答え（理由）を書かない**。一節の一文をそのまま「なぜ〜なのか」で包んだだけの質問にしない。質問は「何がそうなっているか」だけを述べ、「なぜそうしたか」は書かない。
- identifiers には、質問に含めた固有の語をすべて入れる。
- key_points は、正しい答えが含むべき要点を 1〜3 個、日本語の短い文で。一節に書かれていることだけを使う。**質問の言い換えを要点にしない**（要点は、質問が尋ねていない「理由」の中身でなければならない）。
- 一節が「何を決めたか」だけを述べ、「なぜそう決めたか」を述べていないときは answerable を false にする。
- answerable は、一節が理由を述べていれば true、理由を述べていなければ false。
JSON で答えてください。""",
    "S5": """次は、{repo} の文書の一節です（見出し: {heading}）。

{text}

この一節に書かれている手順（何かを行う方法）を尋ねる日本語の質問を 1 つと、その答えの要点を作ってください。
- 質問は「〜するには、どうすればよいか」の形にし、この一節だけから答えられるものにする。
- 質問には、一節に出てくる固有の語（コマンド、設定の名前、画面の項目の名前など）を 1 つ以上、そのまま含める。コマンドや設定の名前はバッククォートで囲む。
- identifiers には、質問に含めた固有の語をすべて入れる。
- key_points は、正しい答えが含むべき手順の要点を 2〜4 個、日本語の短い文で（コマンドや設定の名前はそのまま）。一節に書かれていることだけを使う。
- answerable は、一節が手順を述べていれば true。
JSON で答えてください。""",
}

REPO_JA = {"wikictl": "wikictl（Go の CLI）", "wiki": "wikictl の wiki（日本語の Markdown）", "grafana": "Grafana",
           "linux": "Linux カーネル", "website": "Kubernetes の文書"}


def repo_of(c):
    if c.get("section"):
        return c["section"]["repo"]
    if c.get("gold", {}).get("repo"):
        return c["gold"]["repo"]
    return {"c1": "wikictl", "c2": "grafana", "c3": "linux", "c4": "website"}[c["corpus"]]


def stage_qgen(c):
    sc = c["scenario"]
    if sc == "S2":
        cm = c["commit"]
        text = cm["subject"] + ("\n\n" + cm["body"] if cm["body"] else "")
        prompt = QGEN["S2"].format(repo=REPO_JA[repo_of(c)], text=text,
                                   idents=", ".join(f"`{x}`" for x in c["identifiers"]) or "（なし）")
    else:
        q = c["qgen"]
        prompt = QGEN[sc].format(repo=REPO_JA[repo_of(c)], heading=q["heading"], text=q["text"])
    j = llm.chat_json(llm.GEN_MODEL, prompt, schema=QGEN_SCHEMA)
    if not j or not j.get("question"):
        c["status"] = "qgen_failed"
        return
    c["q_ident"] = j["question"].strip()
    c["identifiers"] = sorted({x for x in (j.get("identifiers") or []) if x.strip()} |
                              ({x for x in c["identifiers"] if x in j["question"]} if sc == "S2" else set()))
    if sc in ("S4", "S5"):
        c["gold"]["points"] = [p for p in j.get("key_points") or [] if p.strip()]
    c["qgen_answerable"] = j.get("answerable")
    if j.get("answerable") is False:
        c["status"] = "qgen_unanswerable"
    if sc == "S2":
        c["subject"] = {"label": c["commit"]["subject"], "kind": "behaviour", "doc": c["commit"]["subject"]}
        c["paraphrase_task"] = "ソフトウェアの、ある振る舞いの実装箇所を尋ねる質問"
    else:
        c["subject"] = {"label": c["section"]["heading"], "kind": "section",
                        "doc": c["section"]["heading"] + ": " + c["qgen"]["text"][:300]}
        c["paraphrase_task"] = {"S4": "設計や方針の理由を尋ねる質問", "S5": "手順を尋ねる質問"}[sc]


# ---------------------------------------------------------------- stage 2

PARA = """次の質問を、指定した語を使わずに言い換えてください。

質問: {q}
使ってはいけない語: {idents}
質問の種類: {task}
対象についての説明（参考）: {doc}

- 使ってはいけない語（大文字・小文字の違い、区切りの有無を問わず、その語の一部分や英単語の直訳のカタカナも）を含めない。ファイルのパスも含めない。
- 対象が何をするもの・何についてのものかを、説明をもとに日本語のふつうの言葉で書き、質問が尋ねていること（どのファイルか、どの関数か、いつか、なぜか、どうするか、など）と答えの対象の範囲は変えない。
- 読んだ人が、答えを一つに決められるだけの手がかりを残す。
- 日本語の 1〜2 文で。
{extra}{feedback}JSON で答えてください。"""

# Rules for some scenarios only, so that the prompts of the others (and
# their cached answers) stay as they were. The sample check of PE2 found
# paraphrases that carried the answer (S4, S5) or said the subject does not
# exist (S8).
PARA_EXTRA = {
    "S4": "- 答え（理由）やその一部を質問の中に書かない。\n",
    "S5": "- 答え（手順）やその一部を質問の中に書かない。\n",
    "S8": "- 対象が架空であること・存在しないことを書かない（実在するものとして尋ねる）。\n",
}
NONEXIST = re.compile(r"架空|存在しない|実在しない|仮想の名前|hypothetical|fictional", re.I)


PRODUCT_NAMES = {"wikictl", "grafana", "kubernetes", "linux", "wiki", "git", "go"}


def leaks(c, text):
    t = text.lower()
    found = []
    for x in c.get("identifiers", []):
        x = x.strip("`").strip()
        if len(x) < 3 or x.lower() in PRODUCT_NAMES:
            continue
        forms = {x.lower()}
        parts = re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+", x)
        if len(parts) > 1:
            forms |= {"_".join(parts).lower(), " ".join(parts).lower(), "-".join(parts).lower()}
        if any(f in t for f in forms):
            found.append(x)
    if re.search(r"`[^`]+`", text):
        found.append("(backticks)")
    if re.search(r"[\w\-]+/[\w\-/]+\.\w+", text):
        found.append("(path)")
    if c["scenario"] == "S8" and NONEXIST.search(text):
        found.append("(says it does not exist)")
    return found


def stage_para(c, feedback=""):
    idents = [x for x in c.get("identifiers", []) if x]
    doc = (c.get("subject") or {}).get("doc", "")[:600]
    if c["scenario"] == "S8":
        # the subject does not exist: describing it from the real name's doc
        # comment made every paraphrase ask about the real one
        doc = (f"実在しない名前 `{c['subject']['label']}` の語の意味から想像される働き。"
               f"実在する {c['subject']['real']} とは別の働きとして書き、その説明は使わない")
    prompt = PARA.format(q=c["q_ident"], idents=", ".join(idents) or "（なし）", task=c.get("paraphrase_task", ""),
                         doc=doc, extra=PARA_EXTRA.get(c["scenario"], ""),
                         feedback=(f"前回の言い換えは次の理由で使えませんでした: {feedback}\n" if feedback else ""))
    j = llm.chat_json(llm.GEN_MODEL, prompt, schema=PARA_SCHEMA)
    q = (j or {}).get("question", "").strip()
    c.setdefault("attempts", []).append({"paraphrase": q})
    lk = leaks(c, q) if q else ["(empty)"]
    if lk:
        c["attempts"][-1]["leak"] = lk
        c["q_para"] = None
        return False
    c["q_para"] = q
    return True


# ---------------------------------------------------------------- stage 3

JUDGE_MC = """ある質問が、次の候補のうちどれについて尋ねているかを判定してください。

質問: {q}

候補:
{cands}

- 質問の内容に最も合う候補の記号を choice に書く。
- 二つ以上の候補が同じくらい当てはまり、質問だけではどれか決められないときは ambiguous を true にする。
- reason に判断の理由を短く書く。
JSON で答えてください。"""

JUDGE_EQ = """二つの質問が、同じことを尋ねていて、同じ一つの答えになるかを判定してください。
質問 B は、質問 A からコードの名前などの固有の語を除いて言い換えたものです。語が無くなったこと自体は問題にしません。

質問 A: {a}
質問 B: {b}
質問 A の対象についての説明: {doc}

- 質問 B が、質問 A と同じ対象・同じ範囲について同じことを尋ね、読んだ人が同じ答えに行き着けるなら same を true にする。
- 質問 B が別のもの・より広いもの・より狭いものを指したり、手がかりが足りず答えが一つに決まらないなら false にする。
- reason に判断の理由を短く書く。
JSON で答えてください。"""

JUDGE_S8 = """次の質問は、実在する次のものについて尋ねていると読めるかを判定してください。

質問: {q}
実在するもの: {real}（説明: {doc}）

- 質問が、実在するものの説明とほぼ同じ機能・対象を指していて、実在するものが答えになってしまうなら same を true にする。
- 質問が実在するものとは別の（異なる働きの）ものを尋ねているなら false にする。
- reason に判断の理由を短く書く。
JSON で答えてください。"""

LETTERS = "ABCDEFGHIJ"


def stage_judge(c, r):
    q = c["q_para"]
    if c["scenario"] == "S8":
        s = c["subject"]
        j = llm.chat_json(llm.CHECK_MODEL, JUDGE_S8.format(q=q, real=s["real"], doc=s["real_doc"][:400]),
                          schema=EQ_SCHEMA)
        ok = bool(j) and j.get("same") is False
        c["judge"] = {"kind": "s8-not-real", "result": j, "keep": ok}
        return ok
    ds = [d for d in c.get("distractors") or [] if d.get("doc")]
    if len(ds) >= 3:
        opts = [{"label": c["subject"]["label"], "doc": c["subject"]["doc"], "gold": True}] + \
               [dict(d, gold=False) for d in ds[:7]]
        r.shuffle(opts)
        lines = [f"{LETTERS[i]}. {o['doc'][:300]}" for i, o in enumerate(opts)]
        j = llm.chat_json(llm.CHECK_MODEL, JUDGE_MC.format(q=q, cands="\n".join(lines)), schema=JUDGE_SCHEMA)
        gold_letter = LETTERS[[o["gold"] for o in opts].index(True)]
        choice = ((j or {}).get("choice") or "").strip()[:1].upper()
        ok = bool(j) and choice == gold_letter and not j.get("ambiguous")
        c["judge"] = {"kind": "multiple-choice", "n_options": len(opts), "gold": gold_letter, "result": j, "keep": ok}
        return ok
    j = llm.chat_json(llm.CHECK_MODEL, JUDGE_EQ.format(a=c["q_ident"], b=q, doc=(c.get("subject") or {}).get("doc", "")[:500]),
                      schema=EQ_SCHEMA)
    ok = bool(j) and j.get("same") is True
    c["judge"] = {"kind": "equivalence", "result": j, "keep": ok}
    return ok


# ---------------------------------------------------------------- stages 5, 6

TRANS = """次の JSON の 3 つの値（日本語）を英語に訳し、同じキーの JSON で返してください。

{src}

- question_identifier と question_paraphrase は質問文、answer_format は答えの形の指定。それぞれの値を訳文だけにし、「Question 1」のような見出しや説明を付けない。
- バッククォートで囲んだ語、コードの名前、パス、コマンド、版は変えずにそのまま残す。
- question_paraphrase には、question_identifier にある識別子を足さない。
- 意味を足したり削ったりしない。自然な英語にする。
JSON で答えてください。"""

TCHECK = """日本語の文と、その英訳を 3 組示します。組ごとに、英訳が日本語と同じ意味かを確認してください。組同士（組 1 と組 2）は別の質問なので、互いに比べないでください。

組 1
  日本語: {qi}
  英訳: {ei}
組 2
  日本語: {qp}
  英訳: {ep}
組 3
  日本語: {fmt}
  英訳: {efmt}

- 各組で、英訳が日本語の意味を足したり削ったりしておらず、コードの名前・パス・版が日本語と同じ綴りで残っていれば、その組は問題なし。
- 3 組とも問題が無ければ ok を true にする。一つでも問題があれば ok を false にし、problem にどの組のどんな問題かを書く。
JSON で答えてください。"""


def stage_translate(c):
    # the three texts go in as JSON under the keys they come back under:
    # with labels such as "質問 1" in the prompt the model returned the labels
    src = json.dumps({"question_identifier": c["q_ident"], "question_paraphrase": c["q_para"],
                      "answer_format": c["answer_format"]}, ensure_ascii=False, indent=1)
    j = llm.chat_json(llm.CHECK_MODEL, TRANS.format(src=src), schema=TR_SCHEMA)
    if not j:
        c["en"] = None
        return
    c["en"] = {"question_identifier": j["question_identifier"].strip(),
               "question_paraphrase": j["question_paraphrase"].strip(), "answer_format": j["answer_format"].strip()}


def stage_tcheck(c):
    e = c["en"]
    j = llm.chat_json(llm.GEN_MODEL, TCHECK.format(qi=c["q_ident"], ei=e["question_identifier"], qp=c["q_para"],
                                                   ep=e["question_paraphrase"], fmt=c["answer_format"],
                                                   efmt=e["answer_format"]), schema=TC_SCHEMA)
    c["en_check"] = j
    # identifiers must survive in the identifier question
    missing = [x for x in re.findall(r"`([^`]+)`", c["q_ident"]) if x not in e["question_identifier"]]
    if missing:
        c["en_check"] = {"ok": False, "problem": f"missing identifiers {missing}", "llm": j}


# ---------------------------------------------------------------- distractors

def add_distractors(cands):
    """Neighbours for the multiple-choice judge, where the candidate files do
    not already carry them: other candidates' subjects of the same scenario
    and corpus, and for sections the other sections of the same page."""
    from cand_docs import sections
    by = collections.defaultdict(list)
    for c in cands:
        by[(c["scenario"], c["corpus"])].append(c)
    for c in cands:
        if c.get("distractors"):
            continue
        ds = []
        if c.get("section") and c["scenario"] in ("S4", "S5"):
            sec = c["section"]
            try:
                _, secs = sections(c["corpus"], sec["repo"], sec["file"])
            except (OSError, ValueError):
                secs = []
            for s in secs:
                if s["line"] != sec["lines"][0] and len(s["text"]) > 80:
                    ds.append({"label": s["heading"], "doc": s["heading"] + ": " + s["text"][:300]})
        if len(ds) < 3:
            for o in by[(c["scenario"], c["corpus"])]:
                if o is not c and o.get("subject"):
                    ds.append({"label": o["subject"]["label"], "doc": o["subject"]["doc"]})
        c["distractors"] = ds[:7]


def go_siblings(cands):
    """For S3 and S6 on Go and C: other functions of the same package (file
    for C) with their doc comments."""
    need = [c for c in cands if c["scenario"] in ("S3", "S6") and c["corpus"] in ("c1", "c2", "c3")
            and not c.get("distractors") and c.get("subject", {}).get("kind") in ("func", "method")]
    if not need:
        return
    from cand_code import C, Go
    gos = {}
    for c in need:
        r = rng(c["split"], "distractors", c["base_id"])
        if c["corpus"] == "c3":
            idx = C()
            f = c["target"]["file"]
            sib = [{"label": d["name"], "doc": d["doc"]} for d in idx.defs if d["file"] == f and d["doc"]
                   and d["name"] != c["subject"]["label"]]
        else:
            g = gos.setdefault(c["corpus"], Go(c["corpus"]))
            area = c["subject"].get("area") or os.path.dirname((c.get("target") or {}).get("file", ""))
            sib = [{"label": d["id"], "doc": d["doc"]} for d in g.defs if not d["test"] and d["doc"]
                   and os.path.dirname(d["file"]) == area and d["id"] != c["subject"]["label"]]
            if not area:
                sib = []
        r.shuffle(sib)
        c["distractors"] = sib[:7]


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--scenario", default="S1,S2,S3,S4,S5,S6,S7,S8,S9")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    cands = []
    for s in a.scenario.split(","):
        for p in sorted(glob.glob(os.path.join(CAND, a.split, f"{s}*.jsonl"))):
            cands += read_jsonl(p)
    if a.limit:
        cands = cands[:a.limit]
    for c in cands:
        c["status"] = "candidate"
        if c["scenario"] == "S6" and c["subject"].get("kind") == "page":
            pass
    go_siblings(cands)
    log = lambda *x: print(*x, file=sys.stderr, flush=True)  # noqa: E731

    # 1. questions from text
    for c in cands:
        if c["scenario"] in ("S2", "S4", "S5"):
            stage_qgen(c)
    log("qgen", llm.stats)
    add_distractors(cands)
    live = [c for c in cands if c["status"] == "candidate"]
    # 2. paraphrase
    for c in live:
        stage_para(c)
    log("para", llm.stats)
    # 3. judge
    for c in live:
        c["judge_rounds"] = []
        if c["q_para"] is None:
            c["judge_rounds"].append("leak")
            continue
        r = rng(c["split"], "judge", c["base_id"])
        ok = stage_judge(c, r)
        c["judge_rounds"].append("keep" if ok else "reject")
    log("judge", llm.stats)
    # 4. one retry
    retry = [c for c in live if c["judge_rounds"][-1] != "keep"]
    for c in retry:
        why = (c.get("judge") or {}).get("result") or {}
        fb = why.get("reason") or ("使ってはいけない語が含まれていた: " + ", ".join(c["attempts"][-1].get("leak", [])))
        stage_para(c, feedback=fb)
    for c in retry:
        if c["q_para"] is None:
            c["judge_rounds"].append("leak")
            continue
        r = rng(c["split"], "judge2", c["base_id"])
        ok = stage_judge(c, r)
        c["judge_rounds"].append("keep" if ok else "reject")
    log("retry", llm.stats)
    for c in live:
        c["status"] = "phrased" if c["judge_rounds"][-1] == "keep" else "paraphrase_rejected"
    kept = [c for c in live if c["status"] == "phrased"]
    # 5. translate (all kept)
    for c in kept:
        stage_translate(c)
    log("translate", llm.stats)
    # 6. check the translation
    for c in kept:
        if c.get("en"):
            stage_tcheck(c)
        else:
            c["en_check"] = {"ok": False, "problem": "no translation"}
    log("tcheck", llm.stats)

    out = collections.defaultdict(list)
    for c in cands:
        out[c["scenario"]].append(c)
    for s, rows in out.items():
        write_jsonl(os.path.join(PHRASED, a.split, f"{s}.jsonl"), rows)
    stats = collections.defaultdict(collections.Counter)
    for c in cands:
        k = f"{c['scenario']}/{c['corpus']}"
        stats[k]["candidates"] += 1
        stats[k][c["status"]] += 1
        rounds = c.get("judge_rounds") or []
        if rounds:
            stats[k]["first_round_keep"] += rounds[0] == "keep"
            stats[k]["first_round_leak"] += rounds[0] == "leak"
        if c.get("en_check"):
            stats[k]["translation_ok"] += bool(c["en_check"].get("ok"))
    json.dump({"stats": {k: dict(v) for k, v in stats.items()}, "llm": llm.stats,
               "models": {"gen": llm.GEN_MODEL, "check": llm.CHECK_MODEL}},
              open(os.path.join(PHRASED, a.split, "stats.json"), "w"), ensure_ascii=False, indent=1)
    log("done", llm.stats)


if __name__ == "__main__":
    main()
