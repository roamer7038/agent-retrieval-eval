#!/usr/bin/env python3
"""Definitions, calls and function references of a C code base, from the ASTs
of the translation units in compile_commands.json (libclang).

    canalyze.py --src /src --db /build/compile_commands.json --out /build/c3-c.jsonl [--jobs 24]

Runs inside the are-gold-clang image (gen/c/Dockerfile), with the corpus at
/src read-only. Records (paths relative to --src):

  {"rec":"def","kind":"func","name","file","line","end","static","inline","doc"}
  {"rec":"call","caller","caller_file","callee","callee_file","file","line"}
  {"rec":"ref","callee","callee_file","context","context_kind","file","line"}
      a function used as a value (address taken, e.g. a struct initializer
      or a function pointer argument): a place a call through a pointer may
      come from
  {"rec":"var","name","type","file","line","static"}   file-scope variables
  {"rec":"tu","file","ok","diags"}
Only code under --src is recorded; headers are walked once per worker.
"""
import argparse
import json
import multiprocessing
import os
import shlex
import sys

import clang.cindex as ci

CK = ci.CursorKind


def lib():
    for p in ("/usr/lib/llvm-19/lib/libclang.so.1", "/usr/lib/llvm-19/lib/libclang.so"):
        if os.path.exists(p):
            ci.Config.set_library_file(p)
            return


def args_of(entry):
    argv = entry.get("arguments") or shlex.split(entry["command"])
    out, skip = [], False
    for a in argv[1:]:
        if skip:
            skip = False
            continue
        if a in ("-o", "-MF", "-MT", "-MQ"):
            skip = True
            continue
        if a in ("-c",) or a.startswith("-Wp,-MMD") or a.startswith("-Wp,-MD"):
            continue
        if a == entry["file"] or os.path.abspath(os.path.join(entry["directory"], a)) == os.path.abspath(
                os.path.join(entry["directory"], entry["file"])):
            continue
        out.append(a)
    return out


def doc_of(c):
    r = c.raw_comment
    if not r:
        return ""
    lines = [l.strip().lstrip("/*").strip() for l in r.splitlines()]
    s = " ".join(l for l in lines if l)
    return s[:400]


class Walker:
    def __init__(self, src, out):
        self.src = os.path.realpath(src) + "/"
        self.out = out
        self.seen_funcs = set()
        self.seen_vars = set()

    def rel(self, f):
        if f is None:
            return None
        p = os.path.realpath(f.name)
        return p[len(self.src):] if p.startswith(self.src) else None

    def put(self, **kw):
        self.out.write(json.dumps(kw, ensure_ascii=False) + "\n")

    def tu(self, tu):
        for c in tu.cursor.get_children():
            loc = c.location
            f = self.rel(loc.file)
            if f is None:
                continue
            if c.kind == CK.FUNCTION_DECL and c.is_definition():
                key = (f, loc.line, c.spelling)
                if key in self.seen_funcs:
                    continue
                self.seen_funcs.add(key)
                self.put(rec="def", kind="func", name=c.spelling, file=f, line=loc.line,
                         end=c.extent.end.line, static=c.storage_class == ci.StorageClass.STATIC,
                         inline=c.is_function_inlined() if hasattr(c, "is_function_inlined") else None,
                         doc=doc_of(c))
                self.body(c, c.spelling, f, "func")
            elif c.kind == CK.VAR_DECL:
                key = (f, loc.line, c.spelling)
                if key in self.seen_vars:
                    continue
                self.seen_vars.add(key)
                self.put(rec="var", name=c.spelling, type=c.type.spelling, file=f, line=loc.line,
                         static=c.storage_class == ci.StorageClass.STATIC, definition=c.is_definition())
                self.body(c, c.spelling, f, "var")

    def body(self, root, ctx, ctx_file, ctx_kind):
        callee_refs = set()
        stack = [root]
        while stack:
            c = stack.pop()
            k = c.kind
            if k == CK.CALL_EXPR:
                ref = c.referenced
                if ref is not None and ref.kind == CK.FUNCTION_DECL:
                    f = self.rel(c.location.file)
                    if f is not None:
                        d = ref.get_definition() or ref
                        self.put(rec="call", caller=ctx, caller_file=ctx_file, callee=ref.spelling,
                                 callee_file=self.rel(d.location.file), file=f, line=c.location.line)
                # The callee expression: the first child, through implicit
                # casts and parentheses.
                x = next(c.get_children(), None)
                while x is not None and x.kind in (CK.UNEXPOSED_EXPR, CK.PAREN_EXPR):
                    x = next(x.get_children(), None)
                if x is not None and x.kind == CK.DECL_REF_EXPR:
                    callee_refs.add(x.hash)
            elif k == CK.DECL_REF_EXPR and c.hash not in callee_refs:
                ref = c.referenced
                if ref is not None and ref.kind == CK.FUNCTION_DECL:
                    f = self.rel(c.location.file)
                    if f is not None:
                        d = ref.get_definition() or ref
                        self.put(rec="ref", callee=ref.spelling, callee_file=self.rel(d.location.file),
                                 context=ctx, context_file=ctx_file, context_kind=ctx_kind, file=f,
                                 line=c.location.line)
            stack.extend(reversed(list(c.get_children())))


def work(job):
    idx, entries, src, outdir = job
    lib()
    index = ci.Index.create()
    path = os.path.join(outdir, f"part-{idx:03d}.jsonl")
    with open(path, "w") as out:
        w = Walker(src, out)
        for e in entries:
            os.chdir(e["directory"])
            try:
                tu = index.parse(e["file"], args=args_of(e))
                diags = [d.spelling for d in tu.diagnostics if d.severity >= ci.Diagnostic.Error]
                full = os.path.realpath(os.path.join(e["directory"], e["file"]))
                w.put(rec="tu", file=full[len(w.src):], ok=not diags, diags=diags[:3])
                w.tu(tu)
            except Exception as ex:  # noqa: BLE001
                w.put(rec="tu", file=e["file"], ok=False, diags=[repr(ex)])
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    entries = [e for e in json.load(open(a.db)) if e["file"].endswith(".c")]
    src = os.path.realpath(a.src) + "/"
    entries = [e for e in entries
               if os.path.realpath(os.path.join(e["directory"], e["file"])).startswith(src)]
    if a.limit:
        entries = entries[:a.limit]
    outdir = a.out + ".parts"
    os.makedirs(outdir, exist_ok=True)
    n = a.jobs
    chunks = [(i, entries[i::n], a.src, outdir) for i in range(n)]
    with multiprocessing.Pool(n) as pool:
        parts = pool.map(work, chunks)
    with open(a.out, "w") as out:
        for p in parts:
            with open(p) as f:
                for line in f:
                    out.write(line)
    print(f"{len(entries)} translation units", file=sys.stderr)


if __name__ == "__main__":
    main()
