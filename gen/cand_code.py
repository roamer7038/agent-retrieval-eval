#!/usr/bin/env python3
"""Candidates for the code scenarios whose gold comes from a compiler-grade
analysis: S1 (where is X defined), S3 (who calls X) and the code part of S7
(enumerate all X).

    gen/cand_code.py [--split dev] [--scenario S1,S3,S7]

Inputs (gold-work/out, written by gen/analyze.sh):
  c1-go.jsonl, c2-go.jsonl       goanalyze (go/packages, go/types, SSA, CHA)
  c1-go-parse.jsonl, c2-go-parse.jsonl   goanalyze -parse-only (all files)
  c2-ts.jsonl                    gen/ts/tsdefs.js (TypeScript parser)
  c3-c.jsonl                     gen/c/canalyze.py (libclang over compile_commands)
Output: gold-work/cand/<split>/S1.jsonl, S3.jsonl, S7-code.jsonl
"""
import argparse
import collections
import json
import os
import re
import subprocess

from common import (CAND, CORPORA, OUT, TEST_EXCL_JA, dropped_base_ids, is_test_path, read_jsonl, rng, split_of,
                    write_jsonl)

N = {"S1": {"c1": 8, "c2": 10, "c3": 8}, "S3": {"c1": 8, "c2": 8, "c3": 8}, "S7": {"c1": 3, "c2": 5, "c3": 5}}
REPO = {"c1": "wikictl", "c2": "grafana", "c3": "linux"}
KIND_JA = {"func": "関数", "method": "メソッド", "struct": "構造体の型", "interface": "インタフェース", "type": "型",
           "function": "関数", "const-function": "関数", "class": "クラス", "enum": "列挙型"}


def load(name):
    path = os.path.join(OUT, name)
    return read_jsonl(path) if os.path.exists(path) else []


def dedupe(rows, key):
    seen, out = set(), []
    for r in rows:
        k = key(r)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


# ---------------------------------------------------------------- Go

class Go:
    def __init__(self, corpus):
        self.corpus = corpus
        recs = load(f"{corpus}-go.jsonl")
        self.defs = dedupe([r for r in recs if r["rec"] == "def"], lambda r: (r["file"], r["line"], r["id"]))
        self.calls = dedupe([r for r in recs if r["rec"] == "call"],
                            lambda r: (r["file"], r["line"], r["caller"], r.get("callee"), r["kind"]))
        self.refs = dedupe([r for r in recs if r["rec"] == "ref"], lambda r: (r["file"], r["line"], r["callee"]))
        self.impls = dedupe([r for r in recs if r["rec"] == "impl"],
                            lambda r: (r["iface_pkg"], r["iface"], r["type_pkg"], r["type"]))
        parse = load(f"{corpus}-go-parse.jsonl")
        self.universe = collections.Counter()
        for p in parse:
            if p["rec"] != "pdef":
                continue
            key = f"{p['recv']}.{p['name']}" if p.get("recv") else p["name"]
            self.universe[key] += 1
        # a caller is looked up here, so a file written for tests (a fake, a
        # mock, a helper) is not a caller: the question says it leaves those
        # out (common.TEST_PATH)
        self.def_by = {(d["pkg"], d["id"]): d for d in self.defs if not d["test"] and not is_test_path(d["file"])}

    def pkgdir(self, d):
        return os.path.dirname(d["file"])

    def s1_pool(self):
        out = []
        for d in self.defs:
            if d["test"] or d["generated"] or d["kind"] not in ("func", "method", "struct", "interface", "type"):
                continue
            if len(d["doc"]) < 40 or d.get("alias") or is_test_path(d["file"]):
                continue
            if self.corpus == "c2" and not d["file"].startswith("pkg/"):
                continue
            if self.universe[d["id"]] != 1:
                continue
            if d["kind"] == "method" and self.universe[d["name"]] > 3:
                # the bare name is shared by many types; keep only methods
                # whose name is distinctive enough to be asked about
                pass
            out.append(d)
        return out

    def direct_callers(self, pkg, fid):
        res = {}
        for c in self.calls:
            if c["kind"] != "static" or c.get("callee_pkg") != pkg or c.get("callee") != fid or c["test"]:
                continue
            if c["caller"] == fid and c["caller_pkg"] == pkg:
                continue
            d = self.def_by.get((c["caller_pkg"], c["caller"]))
            if d is None:
                continue  # package-level initializers
            res[(d["file"], d["id"])] = d
        return res

    def indirect(self, pkg, fid):
        refs = [r for r in self.refs if r["callee_pkg"] == pkg and r["callee"] == fid and not r["test"]]
        cha = [c for c in self.calls if c["kind"] in ("cha", "vta") and c.get("callee_pkg") == pkg
               and c.get("callee") == fid and not c["test"]]
        return refs, cha

    def indirect_callers(self, pkg, fid):
        """Callers through an interface or a function value, as VTA (or CHA)
        resolves them: not direct calls, so the gold lists them as optional."""
        res = {}
        for c in self.calls:
            if c["kind"] not in ("vta", "cha") or c.get("callee_pkg") != pkg or c.get("callee") != fid or c["test"]:
                continue
            d = self.def_by.get((c["caller_pkg"], c["caller"]))
            if d is not None and not (c["caller"] == fid and c["caller_pkg"] == pkg):
                res[(d["file"], d["id"])] = d
        return res


# ---------------------------------------------------------------- C

_C = None


def C():
    global _C
    if _C is None:
        _C = CIndex()
    return _C


class CIndex:
    corpus = "c3"

    def __init__(self):
        recs = load("c3-c.jsonl")
        self.tus = {r["file"] for r in recs if r["rec"] == "tu"}
        self.defs = dedupe([r for r in recs if r["rec"] == "def"], lambda r: (r["file"], r["line"], r["name"]))
        self.calls = dedupe([r for r in recs if r["rec"] == "call"],
                            lambda r: (r["file"], r["line"], r["caller"], r["callee"]))
        self.refs = dedupe([r for r in recs if r["rec"] == "ref"],
                           lambda r: (r["file"], r["line"], r["callee"], r["context"]))
        self.vars = dedupe([r for r in recs if r["rec"] == "var"], lambda r: (r["file"], r["line"], r["name"]))
        self.names = collections.Counter(d["name"] for d in self.defs)
        self.calls_to = collections.defaultdict(list)
        for c in self.calls:
            self.calls_to[c["callee"]].append(c)
        self.def_by = {}
        for d in self.defs:
            self.def_by.setdefault(d["name"], []).append(d)

    def s1_pool(self):
        out = []
        for d in self.defs:
            f = d["file"]
            if not f.endswith(".c") or f.startswith(("arch/", "tools/", "scripts/", "samples/")) or is_test_path(f):
                continue
            if d["static"] or len(d["doc"]) < 40 or self.names[d["name"]] != 1 or not self.literal(d):
                continue
            out.append(d)
        return out

    def same_callee(self, rec, d):
        """A call or reference names d: a static function only from its own
        file; a function with external linkage from any translation unit,
        where libclang points at the declaration in a header when the
        definition is in another unit."""
        if rec["callee"] != d["name"]:
            return False
        return rec["callee_file"] == d["file"] if d["static"] else True

    def direct_callers(self, d):
        res = {}
        for c in self.calls_to.get(d["name"], []):
            if not self.same_callee(c, d):
                continue
            if c["caller"] == d["name"] or is_test_path(c["caller_file"]):
                continue  # a caller written for a test (kunit, selftests) is left out
            res[(c["caller_file"], c["caller"])] = {"file": c["caller_file"], "id": c["caller"]}
        return res

    def indirect(self, d):
        return [r for r in self.refs if self.same_callee(r, d)]

    def unseen_calls(self, d):
        """Lines of .c files that call d by name but that the AST has no call
        for: code the x86_64 defconfig build leaves out (a file outside the
        build, a branch of #ifdef). A static function is looked for in its
        own file, one with external linkage in every .c file. A candidate
        with such lines is dropped, because its gold would miss callers."""
        seen = {(c["file"], c["line"]) for c in self.calls_to.get(d["name"], []) if self.same_callee(c, d)}
        pat = re.compile(rf"\b{re.escape(d['name'])}\s*\(")
        if d.get("static"):
            files = [d["file"]]
        else:
            files = subprocess.run(["git", "-C", os.path.join(CORPORA, "c3/linux"), "grep", "-lwF", d["name"],
                                    "--", "*.c"], capture_output=True, text=True).stdout.split()
        out = []
        for f in files:
            try:
                lines = open(os.path.join(CORPORA, "c3/linux", f), errors="replace").read().split("\n")
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                t = line.strip()
                if not pat.search(line) or t.startswith(("*", "/*", "//", "#define")):
                    continue
                if f == d["file"] and d["line"] <= i <= d["line"] + 2:
                    continue  # the definition
                if (f, i) not in seen:
                    out.append((f, i))
        return out

    def literal(self, d, call=True):
        """The name is written at the definition (not made by a macro such
        as SYSCALL_DEFINE)."""
        try:
            with open(os.path.join(CORPORA, "c3/linux", d["file"]), errors="replace") as f:
                lines = f.read().split("\n")
        except OSError:
            return False
        pat = rf"\b{re.escape(d['name'])}\s*\(" if call else rf"\b{re.escape(d['name'])}\b"
        return any(re.search(pat, l) for l in lines[d["line"] - 1:d["line"] + 2])


C_UNIVERSE = None


def c_defined_elsewhere(name):
    """Definitions of a C function in files outside the build (other
    architectures, drivers not in defconfig): a line that starts with the
    name's return type and the name followed by '(' and does not end with
    ';'. Only used to drop ambiguous S1 targets; the gold itself comes from
    the AST."""
    global C_UNIVERSE
    if C_UNIVERSE is None:
        C_UNIVERSE = collections.defaultdict(list)
        pat = re.compile(r"^(?:[A-Za-z_][\w \*]*?[\s\*])?([A-Za-z_]\w*)\s*\([^;]*$")
        root = os.path.join(CORPORA, "c3/linux")
        for dp, dn, fn in os.walk(root):
            dn[:] = [x for x in dn if x != ".git"]
            for f in fn:
                if not f.endswith((".c", ".h")):
                    continue
                p = os.path.join(dp, f)
                try:
                    for i, line in enumerate(open(p, errors="replace"), 1):
                        if line[:1].isalpha() or line[:1] == "_":
                            m = pat.match(line.rstrip("\n"))
                            if m:
                                C_UNIVERSE[m.group(1)].append((os.path.relpath(p, root), i))
                except OSError:
                    pass
    return C_UNIVERSE.get(name, [])


# ---------------------------------------------------------------- S1

def s1_go(corpus, split, n):
    g = Go(corpus)
    pool = [d for d in g.s1_pool() if split_of(f"{corpus}:S1:{d['file']}:{d['id']}") == split]
    r = rng(split, "S1", corpus)
    r.shuffle(pool)
    out, dropped = [], dropped_base_ids()
    for d in pool:
        if len(out) == n:
            break
        if f"{split}-{corpus}-s1-go-{d['id'].replace('.', '-')}" in dropped:
            continue
        siblings = [x for x in g.s1_pool() if x["pkg"] == d["pkg"] and x["id"] != d["id"]]
        if len(siblings) < 4:
            siblings += [x for x in g.s1_pool() if x["file"].split("/")[:2] == d["file"].split("/")[:2]
                         and x["id"] != d["id"] and x not in siblings]
        r.shuffle(siblings)
        out.append(s1_item(corpus, split, "go", d, d["id"], d["kind"], siblings[:7]))
    return out


def s1_ts(split, n):
    defs = [d for d in load("c2-ts.jsonl") if d["rec"] == "def"]
    names = collections.Counter(d["name"] for d in defs)
    gonames = collections.Counter()
    for p in load("c2-go-parse.jsonl"):
        gonames[p.get("name")] += 1
    pool = [d for d in defs if not d["test"] and d["exported"] and len(d["doc"]) >= 40 and names[d["name"]] == 1
            and gonames[d["name"]] == 0 and d["kind"] != "const"
            and (d["file"].startswith("public/app/") or d["file"].startswith("packages/"))
            and not is_test_path(d["file"]) and not re.search(r"/stories/|\.story\.tsx?$|mock", d["file"])]
    pool = [d for d in pool if split_of(f"c2:S1:{d['file']}:{d['name']}") == split]
    r = rng(split, "S1", "c2", "ts")
    r.shuffle(pool)
    out, dropped = [], dropped_base_ids()
    for d in pool:
        if len(out) == n:
            break
        if f"{split}-c2-s1-ts-{d['name'].replace('.', '-')}" in dropped:
            continue
        d = dict(d, id=d["name"])
        sib = [dict(x, id=x["name"]) for x in defs if os.path.dirname(x["file"]) == os.path.dirname(d["file"])
               and x["name"] != d["name"] and x["doc"]]
        r.shuffle(sib)
        out.append(s1_item("c2", split, "ts", d, d["name"], d["kind"], sib[:7]))
    return out


def s1_c(split, n):
    c = C()
    pool = [d for d in c.s1_pool() if split_of(f"c3:S1:{d['file']}:{d['name']}") == split]
    r = rng(split, "S1", "c3")
    r.shuffle(pool)
    out, dropped = [], dropped_base_ids()
    for d in pool:
        if len(out) == n:
            break
        if f"{split}-c3-s1-c-{d['name'].replace('.', '-')}" in dropped:
            continue
        other = [x for x in c_defined_elsewhere(d["name"]) if x[0] != d["file"]]
        if other:
            continue
        d = dict(d, id=d["name"])
        sib = [dict(x, id=x["name"]) for x in c.defs if x["file"] == d["file"] and x["name"] != d["name"] and x["doc"]]
        r.shuffle(sib)
        out.append(s1_item("c3", split, "c", d, d["name"], "func", sib[:7]))
    return out


def s1_item(corpus, split, lang, d, ident, kind, siblings):
    repo = REPO[corpus]
    kj = KIND_JA.get(kind, "定義")
    if kind == "method":
        recv, name = ident.split(".", 1)
        what = f"型 `{recv}` のメソッド `{name}`"
    else:
        what = f"{kj} `{ident}`"
    q = f"{repo} のソースコードで、{what} はどのファイルに定義されているか。"
    return {
        "base_id": f"{split}-{corpus}-s1-{lang}-{ident.replace('.', '-')}",
        "corpus": corpus, "scenario": "S1", "split": split, "code_lang": lang,
        "q_ident": q,
        "identifiers": [ident] + ([ident.split(".", 1)[1]] if "." in ident else []),
        "subject": {"label": ident, "kind": kind, "doc": d["doc"], "area": os.path.dirname(d["file"])},
        "distractors": [{"label": s["id"], "doc": s["doc"]} for s in siblings],
        "paraphrase_task": f"{repo} のソースコードで、次の{kj}がどのファイルに定義されているかを尋ねる質問",
        "answer_format": f"{repo} リポジトリの根からのファイルのパス（例: `{example_path(corpus)}`）",
        "gold": {"type": "path", "repo": repo, "value": d["file"], "lines": [d["line"], d["end"]]},
        "evidence_accept": [f"{repo}/{d['file']}"],
        "review": {"required": False, "sample": True,
                   "check": "定義の位置（ファイルと行）が正しいか。同名の定義が別のファイル（ビルドに入らないもの、テスト）に無いか"},
        "source": {"analyzer": {"go": "goanalyze", "ts": "tsdefs.js", "c": "canalyze.py"}[lang]},
    }


def example_path(corpus):
    return {"c1": "internal/cli/cli.go", "c2": "pkg/api/api.go", "c3": "kernel/fork.c"}[corpus]


# ---------------------------------------------------------------- S3

def s3_go(corpus, split, n):
    g = Go(corpus)
    r = rng(split, "S3", corpus)
    cands = []
    for d in g.defs:
        if d["test"] or d["generated"] or d["kind"] not in ("func", "method") or len(d["doc"]) < 30:
            continue
        if corpus == "c2" and (d["exported"] or not d["file"].startswith("pkg/")):
            continue  # unexported: every caller is in the same package, which was loaded
        if g.universe[d["id"]] != 1:
            continue
        if split_of(f"{corpus}:S3:{d['file']}:{d['id']}") != split:
            continue
        cands.append(d)
    r.shuffle(cands)
    out, dropped = [], dropped_base_ids()
    depth_plan = [1, 2] * n
    for d in cands:
        if len(out) == n:
            break
        depth = depth_plan[len(out)]
        if f"{split}-{corpus}-s3-go-{d['id'].replace('.', '-')}-d{depth}" in dropped:
            continue
        l1 = g.direct_callers(d["pkg"], d["id"])
        if not 2 <= len(l1) <= 8:
            continue
        gold = dict(l1)
        optional = dict(g.indirect_callers(d["pkg"], d["id"]))
        if depth == 2:
            for (f, cid), cd in l1.items():
                if corpus == "c2" and cd["exported"]:
                    gold = None
                    break
                gold.update(g.direct_callers(cd["pkg"], cd["id"]))
                optional.update(g.indirect_callers(cd["pkg"], cd["id"]))
            if gold is None or len(gold) > 12 or len(gold) == len(l1):
                continue
            gold.pop((d["file"], d["id"]), None)
        for k in list(optional):
            if k in gold or k == (d["file"], d["id"]):
                optional.pop(k)
        refs, cha = g.indirect(d["pkg"], d["id"])
        out.append(s3_item(corpus, split, "go", d, d["id"], d["kind"], depth, gold, refs, cha,
                           optional=optional))
    return out


def s3_c(split, n):
    c = C()
    r = rng(split, "S3", "c3")
    cands = [d for d in c.defs if d["file"].endswith(".c") and not d["file"].startswith(("arch/", "tools/"))
             and not is_test_path(d["file"]) and len(d["doc"]) >= 30 and c.names[d["name"]] == 1
             and split_of(f"c3:S3:{d['file']}:{d['name']}") == split]
    r.shuffle(cands)
    out, dropped = [], dropped_base_ids()
    # static functions only: every caller is in the same file, which is in
    # the build; a function with external linkage may also be called from
    # files the x86_64 defconfig does not build, which the AST cannot see
    plan = [(True, 1), (True, 1), (True, 2)] * n
    for d in cands:
        if len(out) == n:
            break
        want_static, depth = plan[len(out)]
        if f"{split}-c3-s3-c-{d['name']}-d{depth}" in dropped:
            continue  # audited as wrong or invalid: draw another candidate
        if d["static"] != want_static or not c.literal(d):
            continue
        l1 = c.direct_callers(d)
        if not 2 <= len(l1) <= 8:
            continue
        if c.unseen_calls(d):
            continue
        gold = dict(l1)
        if depth == 2:
            for key, cd in l1.items():
                for x in c.def_by.get(cd["id"], []):
                    if x["file"] == cd["file"]:
                        if c.unseen_calls(x):
                            gold = None
                            break
                        gold.update(c.direct_callers(x))
                if gold is None:
                    break
            if gold is None:
                continue
            gold.pop((d["file"], d["name"]), None)
            if len(gold) > 12 or len(gold) == len(l1):
                continue
        refs = c.indirect(d)
        optional = {(r["context_file"], r["context"]): r for r in refs
                    if r["context_kind"] == "func" and not is_test_path(r["context_file"])}
        d = dict(d, id=d["name"])
        out.append(s3_item("c3", split, "c", d, d["name"], "func", depth, gold, refs, [],
                           scope="", optional={k: v for k, v in optional.items() if k not in gold}))
    return out


def s3_item(corpus, split, lang, d, ident, kind, depth, gold, refs, cha, scope="", optional=None):
    repo = REPO[corpus]
    if kind == "method":
        recv, name = ident.split(".", 1)
        what = f"型 `{recv}` のメソッド `{name}`"
    else:
        what = f"関数 `{ident}`"
    if depth == 1:
        rel = "直接呼び出している"
    else:
        rel = "直接呼び出している関数と、それらを直接呼び出している関数（2 段まで）の"
    # the gold leaves out the files written for tests and the stand-ins they
    # use (common.TEST_PATH), so the question says which files those are
    excl = TEST_EXCL_JA
    q = (f"{repo} のソースコード（{excl}）で、{scope}{what} を{rel}関数をすべて挙げよ。" if depth == 1 else
         f"{repo} のソースコード（{excl}）で、{scope}{what} を{rel}すべてを挙げよ。")
    items = sorted({(f, i) for (f, i) in gold})
    return {
        "base_id": f"{split}-{corpus}-s3-{lang}-{ident.replace('.', '-')}-d{depth}",
        "corpus": corpus, "scenario": "S3", "split": split, "code_lang": lang, "depth": depth,
        "q_ident": q,
        "identifiers": [ident] + ([ident.split(".", 1)[1]] if "." in ident else []),
        "subject": {"label": ident, "kind": kind, "doc": d["doc"], "area": os.path.dirname(d["file"])},
        "paraphrase_task": f"{repo} のソースコードで、次の関数を{'直接呼び出している関数' if depth == 1 else '2 段までさかのぼって呼び出している関数'}をすべて挙げさせる質問",
        "answer_format": "関数の一覧。各要素は `ファイルのパス:関数名`（メソッドは `ファイルのパス:型名.メソッド名`。パスはリポジトリの根から）",
        "gold": {"type": "set", "match": "func", "repo": repo,
                 "value": [f"{f}:{i}" for f, i in items],
                 "optional": sorted(f"{f}:{i}" for f, i in (optional or {}))},
        "evidence_accept": sorted({f"{repo}/{f}" for f, _ in items} | {f"{repo}/{d['file']}"}),
        "target": {"file": d["file"], "line": d["line"]},
        "review": {"required": False, "sample": True,
                   "check": "呼び出し元の漏れ（関数ポインタ・インタフェース経由、ビルドに入らない条件コンパイル、マクロ）と余分",
                   "machine_flags": {"function_value_uses": len(refs), "cha_edges": len(cha)}},
        "source": {"analyzer": {"go": "goanalyze (SSA static callees)", "c": "canalyze.py (libclang CALL_EXPR)"}[lang]},
    }


# ---------------------------------------------------------------- S7 (code)

def s7_go(corpus, split, n):
    g = Go(corpus)
    r = rng(split, "S7", corpus)
    repo = REPO[corpus]
    fams = []
    # 1. types that implement an interface of the corpus
    by_iface = collections.defaultdict(list)
    for im in g.impls:
        # a fake or a mock implements the interface too; the question says it
        # leaves those out, so the gold does the same (common.TEST_PATH)
        if is_test_path(im["file"]) or (corpus == "c2" and not im["file"].startswith("pkg/")):
            continue
        by_iface[(im["iface_pkg"], im["iface"])].append(im)
    idefs = {(d["pkg"], d["id"]): d for d in g.defs if d["kind"] == "interface" and not d["test"]}
    for key, ims in by_iface.items():
        d = idefs.get(key)
        if not d or not 2 <= len(ims) <= 15 or g.universe[d["id"]] != 1:
            continue
        fams.append(("impl", d, sorted({f"{im['file']}:{im['type']}" for im in ims})))
    # 2. methods of a type. The question asks for every method of the type
    # and leaves out only what it names (the files written for tests), so the
    # gold counts the generated methods too; whether a type is drawn at all is
    # still decided on the methods written by hand, so that this does not
    # change which families the draw sees (PE2d).
    by_recv, all_recv = collections.defaultdict(list), collections.defaultdict(list)
    for d in g.defs:
        if d["kind"] == "method" and not d["test"] and not is_test_path(d["file"]):
            all_recv[(d["pkg"], d["recv"])].append(d)
            if not d["generated"]:
                by_recv[(d["pkg"], d["recv"])].append(d)
    tdefs = {(d["pkg"], d["id"]): d for d in g.defs
             if d["kind"] in ("struct", "type") and not d["test"] and not is_test_path(d["file"])}
    for key, ms in by_recv.items():
        d = tdefs.get(key)
        if not d or not 4 <= len(ms) <= 20 or g.universe[d["id"]] != 1:
            continue
        if corpus == "c2" and not d["file"].startswith("pkg/"):
            continue
        fams.append(("methods", d, sorted({m["name"] for m in all_recv[key]})))
    fams = [f for f in fams if split_of(f"{corpus}:S7:{f[0]}:{f[1]['file']}:{f[1]['id']}") == split]
    r.shuffle(fams)
    out, dropped = [], dropped_base_ids()
    for kind, d, items in fams:
        if len(out) == n:
            break
        if f"{split}-{corpus}-s7-go-{kind}-{d['id']}" in dropped:
            continue
        if kind == "impl":
            q = (f"{repo} のソースコード（{TEST_EXCL_JA}{'。pkg/ 以下' if corpus == 'c2' else ''}）で、インタフェース `{d['id']}`"
                 f"（{d['file']}）を満たす名前付きの型をすべて挙げよ。")
            fmt = "型の一覧。各要素は `ファイルのパス:型名`（パスはリポジトリの根から）"
            gold = {"type": "set", "match": "func", "repo": repo, "value": items}
            ptask = "次のインタフェースを満たす型をすべて挙げさせる質問"
            ev = sorted({f"{repo}/{x.split(':')[0]}" for x in items})
        else:
            q = f"{repo} のソースコード（{TEST_EXCL_JA}）で、型 `{d['id']}` に定義されているメソッドをすべて挙げよ。"
            fmt = "メソッド名の一覧"
            gold = {"type": "set", "match": "name", "value": items}
            ptask = "次の型に定義されているメソッドをすべて挙げさせる質問"
            ev = sorted({f"{repo}/{m['file']}" for m in all_recv[(d['pkg'], d['id'])]})
        out.append({
            "base_id": f"{split}-{corpus}-s7-go-{kind}-{d['id']}",
            "corpus": corpus, "scenario": "S7", "split": split, "code_lang": "go", "family": kind,
            "q_ident": q, "identifiers": [d["id"]],
            "subject": {"label": d["id"], "kind": d["kind"], "doc": d["doc"], "area": os.path.dirname(d["file"])},
            "paraphrase_task": f"{repo} のソースコードで、{ptask}",
            "answer_format": fmt, "gold": gold, "evidence_accept": ev,
            "review": {"required": False, "sample": True,
                       "check": "全件が挙がっているか（ビルドタグで外れるファイル、生成コード、別モジュール）"},
            "source": {"analyzer": "goanalyze (types.Implements / method sets)"},
        })
    return out


def s7_c(split, n):
    c = C()
    r = rng(split, "S7", "c3")
    fams = []
    # variables of a struct type defined in a directory whose .c files are all built
    built_dirs = collections.defaultdict(set)
    for t in c.tus:
        built_dirs[os.path.dirname(t)].add(t)
    root = os.path.join(CORPORA, "c3/linux")
    by = collections.defaultdict(list)
    for v in c.vars:
        if not v.get("definition") or not v["file"].endswith(".c") or is_test_path(v["file"]):
            continue
        m = re.match(r"^(?:const )?(struct \w+)$", v["type"])
        if not m or not c.literal(dict(v, name=v["name"]), call=False):
            continue  # names made by macros (DEFINE_SHOW_ATTRIBUTE, module_param) are not in the text
        by[(os.path.dirname(v["file"]), m.group(1))].append(v)
    for (dr, ty), vs in by.items():
        if dr.startswith(("arch/", "tools/")) or not 3 <= len(vs) <= 15:
            continue
        allc = {os.path.join(dr, f) for f in os.listdir(os.path.join(root, dr)) if f.endswith(".c")}
        if not allc or allc - built_dirs.get(dr, set()):
            continue  # some .c file in the directory is not in the build
        fams.append(("vars", dr, ty, sorted({f"{v['file']}:{v['name']}" for v in vs})))
    fams = [f for f in fams if split_of(f"c3:S7:{f[1]}:{f[2]}") == split]
    r.shuffle(fams)
    out, dropped = [], dropped_base_ids()
    for _, dr, ty, items in fams:
        if len(out) == n:
            break
        if f"{split}-c3-s7-c-{dr.replace('/', '-')}-{ty.split()[1]}" in dropped:
            continue
        out.append({
            "base_id": f"{split}-c3-s7-c-{dr.replace('/', '-')}-{ty.split()[1]}",
            "corpus": "c3", "scenario": "S7", "split": split, "code_lang": "c", "family": "vars",
            "q_ident": f"linux のソースコードの `{dr}/` 直下の .c ファイル（{TEST_EXCL_JA}）で、`{ty}` 型のファイルスコープの変数として定義されているものをすべて挙げよ。",
            "identifiers": [ty.split()[1], dr],
            "subject": {"label": f"{dr}: {ty}", "kind": "struct-vars", "doc": "", "area": dr},
            "paraphrase_task": "linux のソースコードの、あるディレクトリで、ある構造体の型の変数をすべて挙げさせる質問",
            "answer_format": "変数の一覧。各要素は `ファイルのパス:変数名`（パスはリポジトリの根から）",
            "gold": {"type": "set", "match": "func", "repo": "linux", "value": items},
            "evidence_accept": sorted({f"linux/{x.split(':')[0]}" for x in items}),
            "review": {"required": False, "sample": True,
                       "check": "defconfig で無効な #ifdef の中の定義、マクロで生成される定義の漏れ"},
            "source": {"analyzer": "canalyze.py (VAR_DECL)"},
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--scenario", default="S1,S3,S7")
    a = ap.parse_args()
    sc = a.scenario.split(",")
    d = os.path.join(CAND, a.split)
    if "S1" in sc:
        rows = s1_go("c1", a.split, N["S1"]["c1"]) + s1_go("c2", a.split, N["S1"]["c2"] // 2) + \
            s1_ts(a.split, N["S1"]["c2"] - N["S1"]["c2"] // 2) + s1_c(a.split, N["S1"]["c3"])
        write_jsonl(os.path.join(d, "S1.jsonl"), rows)
        print("S1", collections.Counter(r["corpus"] for r in rows))
    if "S3" in sc:
        rows = s3_go("c1", a.split, N["S3"]["c1"]) + s3_go("c2", a.split, N["S3"]["c2"]) + s3_c(a.split, N["S3"]["c3"])
        write_jsonl(os.path.join(d, "S3.jsonl"), rows)
        print("S3", collections.Counter(r["corpus"] for r in rows))
    if "S7" in sc:
        rows = s7_go("c1", a.split, N["S7"]["c1"]) + s7_go("c2", a.split, N["S7"]["c2"]) + s7_c(a.split, N["S7"]["c3"])
        write_jsonl(os.path.join(d, "S7-code.jsonl"), rows)
        print("S7", collections.Counter(r["corpus"] for r in rows))


if __name__ == "__main__":
    main()
