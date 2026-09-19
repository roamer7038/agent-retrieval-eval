// Command goanalyze writes the definitions and call edges of a Go module as
// JSON lines, from the type checker and SSA of golang.org/x/tools. It is the
// source of the gold answers for S1 (definitions), S3 (callers) and S7
// (enumerations) on the Go corpora, and does not use any of the retrieval
// tools under evaluation.
//
//	goanalyze -dir <module root> -out <file.jsonl> [-cha] [-vta] <package patterns>...
//
// Records:
//
//	{"rec":"def", ...}   a top-level func, method, type, const or var
//	{"rec":"call", ...}  a call site: caller, callee and whether the call is
//	                     static (the callee is known at compile time),
//	                     invoke (an interface method call, resolved by CHA to
//	                     each concrete method when -cha is given) or dynamic
//	                     (a call through a function value); with -vta, the
//	                     dynamic and invoke sites resolved by VTA ("vta")
//	{"rec":"ref", ...}   a use of a function as a value (not called there):
//	                     the places where a call through a function value
//	                     may come from
//	{"rec":"impl", ...}  a named type that implements a named interface of the
//	                     loaded packages
//	{"rec":"error", ...} a package that did not type-check
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"go/types"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"golang.org/x/tools/go/callgraph"
	"golang.org/x/tools/go/callgraph/cha"
	"golang.org/x/tools/go/callgraph/vta"
	"golang.org/x/tools/go/packages"
	"golang.org/x/tools/go/ssa"
	"golang.org/x/tools/go/ssa/ssautil"
)

type out struct {
	w   *bufio.Writer
	enc *json.Encoder
}

func (o *out) put(v any) {
	if err := o.enc.Encode(v); err != nil {
		panic(err)
	}
}

var root string

func rel(p string) string {
	r, err := filepath.Rel(root, p)
	if err != nil || strings.HasPrefix(r, "..") {
		return ""
	}
	return filepath.ToSlash(r)
}

// funcName is the name of a function as the gold writes it: Func,
// Recv.Method, or Outer$1 for a closure (the closure is reported under its
// enclosing named function as well).
func funcID(f *types.Func) string {
	sig := f.Type().(*types.Signature)
	if r := sig.Recv(); r != nil {
		t := r.Type()
		if p, ok := t.(*types.Pointer); ok {
			t = p.Elem()
		}
		switch n := t.(type) {
		case *types.Named:
			return n.Obj().Name() + "." + f.Name()
		case *types.Alias:
			return n.Obj().Name() + "." + f.Name()
		case *types.Interface:
			return "interface." + f.Name()
		}
		return types.TypeString(t, func(*types.Package) string { return "" }) + "." + f.Name()
	}
	return f.Name()
}

func pkgPath(o types.Object) string {
	if o.Pkg() == nil {
		return ""
	}
	return o.Pkg().Path()
}

// named returns the outermost named function of an SSA function (closures
// map to the function that contains them) and whether fn was a closure.
func named(fn *ssa.Function) (*ssa.Function, bool) {
	closure := false
	for fn.Parent() != nil {
		fn = fn.Parent()
		closure = true
	}
	return fn, closure
}

func ssaID(fn *ssa.Function) (pkg, id string) {
	if o, ok := fn.Object().(*types.Func); ok && o != nil {
		return pkgPath(o), funcID(o)
	}
	if fn.Pkg != nil {
		return fn.Pkg.Pkg.Path(), fn.Name()
	}
	return "", fn.String()
}

func docLine(g *ast.CommentGroup) string {
	if g == nil {
		return ""
	}
	s := strings.TrimSpace(g.Text())
	if i := strings.Index(s, "\n\n"); i >= 0 {
		s = s[:i]
	}
	s = strings.Join(strings.Fields(s), " ")
	if len(s) > 400 {
		s = s[:400]
	}
	return s
}

func main() {
	dir := flag.String("dir", ".", "module root")
	outPath := flag.String("out", "-", "output file")
	withCHA := flag.Bool("cha", false, "resolve interface method calls with CHA")
	withVTA := flag.Bool("vta", false, "resolve calls through interfaces and function values with VTA (on top of CHA)")
	tests := flag.Bool("tests", false, "include test files")
	parseOnly := flag.Bool("parse-only", false, "parse every .go file under -dir (all build tags, tests, nested modules) and write only the top-level declarations")
	flag.Parse()
	root, _ = filepath.Abs(*dir)
	if *parseOnly {
		parseAll(*outPath)
		return
	}

	var f *os.File
	if *outPath == "-" {
		f = os.Stdout
	} else {
		var err error
		if f, err = os.Create(*outPath); err != nil {
			panic(err)
		}
		defer f.Close()
	}
	o := &out{w: bufio.NewWriterSize(f, 1<<20)}
	o.enc = json.NewEncoder(o.w)
	o.enc.SetEscapeHTML(false)
	defer o.w.Flush()

	cfg := &packages.Config{
		Mode: packages.NeedName | packages.NeedFiles | packages.NeedSyntax | packages.NeedTypes |
			packages.NeedTypesInfo | packages.NeedImports | packages.NeedDeps | packages.NeedModule,
		Dir:   root,
		Tests: *tests,
	}
	pkgs, err := packages.Load(cfg, flag.Args()...)
	if err != nil {
		panic(err)
	}
	fmt.Fprintf(os.Stderr, "loaded %d packages\n", len(pkgs))

	// Packages of the module under analysis (not the dependencies).
	local := map[*types.Package]bool{}
	var locals []*packages.Package
	for _, p := range pkgs {
		if len(p.Errors) > 0 {
			msgs := []string{}
			for i, e := range p.Errors {
				if i == 5 {
					break
				}
				msgs = append(msgs, e.Error())
			}
			o.put(map[string]any{"rec": "error", "pkg": p.PkgPath, "n": len(p.Errors), "errors": msgs})
		}
		if p.Types != nil {
			local[p.Types] = true
			locals = append(locals, p)
		}
	}

	// Definitions, and uses of functions as values.
	fset := pkgs[0].Fset
	pos := func(p token.Pos) (string, int) {
		ps := fset.Position(p)
		return rel(ps.Filename), ps.Line
	}
	var ifaces []*types.TypeName
	var namedTypes []*types.TypeName
	for _, p := range locals {
		for _, file := range p.Syntax {
			fn, _ := pos(file.Pos())
			if fn == "" {
				continue
			}
			isTest := strings.HasSuffix(fn, "_test.go")
			generated := ast.IsGenerated(file)
			for _, d := range file.Decls {
				switch d := d.(type) {
				case *ast.FuncDecl:
					obj, _ := p.TypesInfo.Defs[d.Name].(*types.Func)
					if obj == nil {
						continue
					}
					_, l1 := pos(d.Pos())
					_, l2 := pos(d.End())
					kind := "func"
					recv := ""
					if d.Recv != nil {
						kind = "method"
						recv = strings.SplitN(funcID(obj), ".", 2)[0]
					}
					o.put(map[string]any{"rec": "def", "kind": kind, "pkg": p.PkgPath, "name": d.Name.Name,
						"id": funcID(obj), "recv": recv, "file": fn, "line": l1, "end": l2,
						"exported": d.Name.IsExported(), "test": isTest, "generated": generated,
						"doc": docLine(d.Doc), "sig": types.TypeString(obj.Type(), types.RelativeTo(p.Types))})
				case *ast.GenDecl:
					for _, s := range d.Specs {
						switch s := s.(type) {
						case *ast.TypeSpec:
							obj, _ := p.TypesInfo.Defs[s.Name].(*types.TypeName)
							if obj == nil {
								continue
							}
							_, l1 := pos(s.Pos())
							_, l2 := pos(s.End())
							tk := "type"
							if _, ok := obj.Type().Underlying().(*types.Interface); ok {
								tk = "interface"
								if !obj.IsAlias() {
									ifaces = append(ifaces, obj)
								}
							} else if _, ok := obj.Type().Underlying().(*types.Struct); ok {
								tk = "struct"
							}
							if !obj.IsAlias() && !isTest {
								namedTypes = append(namedTypes, obj)
							}
							doc := s.Doc
							if doc == nil {
								doc = d.Doc
							}
							o.put(map[string]any{"rec": "def", "kind": tk, "pkg": p.PkgPath, "name": s.Name.Name,
								"id": s.Name.Name, "file": fn, "line": l1, "end": l2, "exported": s.Name.IsExported(),
								"test": isTest, "generated": generated, "doc": docLine(doc), "alias": obj.IsAlias()})
						case *ast.ValueSpec:
							kind := "var"
							if d.Tok == token.CONST {
								kind = "const"
							}
							doc := s.Doc
							if doc == nil && len(d.Specs) == 1 {
								doc = d.Doc
							}
							for _, n := range s.Names {
								if n.Name == "_" {
									continue
								}
								_, l1 := pos(n.Pos())
								rec := map[string]any{"rec": "def", "kind": kind, "pkg": p.PkgPath, "name": n.Name,
									"id": n.Name, "file": fn, "line": l1, "end": l1, "exported": n.IsExported(),
									"test": isTest, "generated": generated, "doc": docLine(doc)}
								if c, ok := p.TypesInfo.Defs[n].(*types.Const); ok {
									rec["value"] = c.Val().ExactString()
								}
								o.put(rec)
							}
						}
					}
				}
			}
			// Functions used as values: every identifier that refers to a
			// func and is not the Fun of a call expression.
			called := map[*ast.Ident]bool{}
			ast.Inspect(file, func(n ast.Node) bool {
				if c, ok := n.(*ast.CallExpr); ok {
					switch f := ast.Unparen(c.Fun).(type) {
					case *ast.Ident:
						called[f] = true
					case *ast.SelectorExpr:
						called[f.Sel] = true
					case *ast.IndexExpr:
						if id, ok := f.X.(*ast.Ident); ok {
							called[id] = true
						} else if se, ok := f.X.(*ast.SelectorExpr); ok {
							called[se.Sel] = true
						}
					}
				}
				return true
			})
			var stack []ast.Node
			ast.Inspect(file, func(n ast.Node) bool {
				if n == nil {
					stack = stack[:len(stack)-1]
					return true
				}
				stack = append(stack, n)
				id, ok := n.(*ast.Ident)
				if !ok || called[id] {
					return true
				}
				fo, ok := p.TypesInfo.Uses[id].(*types.Func)
				if !ok || !local[fo.Pkg()] {
					return true
				}
				// the enclosing top-level function
				encl := ""
				for i := len(stack) - 1; i >= 0; i-- {
					if fd, ok := stack[i].(*ast.FuncDecl); ok {
						if eo, ok := p.TypesInfo.Defs[fd.Name].(*types.Func); ok {
							encl = funcID(eo)
						}
						break
					}
				}
				f, l := pos(id.Pos())
				o.put(map[string]any{"rec": "ref", "callee_pkg": pkgPath(fo), "callee": funcID(fo),
					"caller_pkg": p.PkgPath, "caller": encl, "file": f, "line": l, "test": isTest})
				return true
			})
		}
	}

	// Implementations among the loaded packages' named types.
	sort.Slice(ifaces, func(i, j int) bool { return ifaces[i].Id() < ifaces[j].Id() })
	for _, it := range ifaces {
		iface := it.Type().Underlying().(*types.Interface)
		if iface.NumMethods() == 0 {
			continue
		}
		for _, nt := range namedTypes {
			if nt == it {
				continue
			}
			t := nt.Type()
			if types.IsInterface(t) {
				continue
			}
			ptr := false
			if !types.Implements(t, iface) {
				if !types.Implements(types.NewPointer(t), iface) {
					continue
				}
				ptr = true
			}
			f, l := pos(nt.Pos())
			o.put(map[string]any{"rec": "impl", "iface_pkg": pkgPath(it), "iface": it.Name(),
				"type_pkg": pkgPath(nt), "type": nt.Name(), "pointer": ptr, "file": f, "line": l})
		}
	}

	// Call edges from SSA.
	prog, _ := ssautil.AllPackages(pkgs, ssa.InstantiateGenerics)
	prog.Build()
	fmt.Fprintf(os.Stderr, "ssa built\n")
	emit := func(caller *ssa.Function, site ssa.CallInstruction, callee *ssa.Function, kind string) {
		if caller.Pkg == nil || !local[caller.Pkg.Pkg] {
			return
		}
		f, l := pos(site.Pos())
		if f == "" {
			return
		}
		top, closure := named(caller)
		cp, cid := ssaID(top)
		rec := map[string]any{"rec": "call", "kind": kind, "caller_pkg": cp, "caller": cid, "closure": closure,
			"file": f, "line": l, "test": strings.HasSuffix(f, "_test.go")}
		if callee != nil {
			if callee.Origin() != nil {
				callee = callee.Origin()
			}
			ep, eid := ssaID(callee)
			if callee.Parent() != nil {
				t, _ := named(callee)
				ep, eid = ssaID(t)
				eid += "$closure"
			}
			rec["callee_pkg"], rec["callee"] = ep, eid
		} else if site.Common().IsInvoke() {
			m := site.Common().Method
			rec["callee_pkg"], rec["callee"] = pkgPath(m), "interface."+m.Name()
			rec["iface"] = types.TypeString(site.Common().Value.Type(), nil)
		}
		o.put(rec)
	}
	for fn := range ssautil.AllFunctions(prog) {
		if fn.Pkg == nil || !local[fn.Pkg.Pkg] {
			continue
		}
		for _, b := range fn.Blocks {
			for _, ins := range b.Instrs {
				site, ok := ins.(ssa.CallInstruction)
				if !ok {
					continue
				}
				c := site.Common()
				if callee := c.StaticCallee(); callee != nil {
					emit(fn, site, callee, "static")
				} else if c.IsInvoke() {
					emit(fn, site, nil, "invoke")
				} else {
					emit(fn, site, nil, "dynamic")
				}
			}
		}
	}
	if *withVTA {
		cg := vta.CallGraph(ssautil.AllFunctions(prog), cha.CallGraph(prog))
		callgraph.GraphVisitEdges(cg, func(e *callgraph.Edge) error {
			if e.Site == nil || e.Site.Common().StaticCallee() != nil {
				return nil
			}
			if !isLocal(e.Callee.Func, local) {
				return nil
			}
			emit(e.Caller.Func, e.Site, e.Callee.Func, "vta")
			return nil
		})
	}
	if *withCHA {
		cg := cha.CallGraph(prog)
		callgraph.GraphVisitEdges(cg, func(e *callgraph.Edge) error {
			if e.Site == nil || e.Site.Common().StaticCallee() != nil {
				return nil
			}
			if !isLocal(e.Callee.Func, local) {
				return nil
			}
			emit(e.Caller.Func, e.Site, e.Callee.Func, "cha")
			return nil
		})
	}
}

var (
	litRe = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_.-]{2,60}$`)
	tagRe = regexp.MustCompile(`(\w+):"([A-Za-z0-9_.-]+)[,"]`)
)

// parseAll lists the top-level declarations of every .go file under root
// with go/parser alone, whatever the build tags and modules: the universe in
// which a name must be unique for an S1 question.
func parseAll(outPath string) {
	f := os.Stdout
	if outPath != "-" {
		var err error
		if f, err = os.Create(outPath); err != nil {
			panic(err)
		}
		defer f.Close()
	}
	w := bufio.NewWriter(f)
	defer w.Flush()
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	fset := token.NewFileSet()
	filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if d.IsDir() {
			n := d.Name()
			if n == ".git" || n == "node_modules" || n == "testdata" {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(p, ".go") {
			return nil
		}
		file, err := parser.ParseFile(fset, p, nil, parser.SkipObjectResolution)
		if err != nil {
			enc.Encode(map[string]any{"rec": "parse_error", "file": rel(p)})
			return nil
		}
		r := rel(p)
		// Identifier-like string literals and struct tag names: the code side
		// of an S9 link from a term the documents mention (a setting key, a
		// rule name, a command).
		ast.Inspect(file, func(n ast.Node) bool {
			switch n := n.(type) {
			case *ast.BasicLit:
				if n.Kind == token.STRING {
					if v, err := strconv.Unquote(n.Value); err == nil && litRe.MatchString(v) {
						enc.Encode(map[string]any{"rec": "lit", "value": v, "file": r, "line": fset.Position(n.Pos()).Line})
					}
				}
				return false
			case *ast.Field:
				if n.Tag != nil {
					if v, err := strconv.Unquote(n.Tag.Value); err == nil {
						for _, m := range tagRe.FindAllStringSubmatch(v, -1) {
							enc.Encode(map[string]any{"rec": "lit", "value": m[2], "tag": m[1], "file": r,
								"line": fset.Position(n.Pos()).Line})
						}
					}
				}
			}
			return true
		})
		for _, d := range file.Decls {
			switch d := d.(type) {
			case *ast.FuncDecl:
				recv := ""
				if d.Recv != nil && len(d.Recv.List) > 0 {
					t := d.Recv.List[0].Type
					if s, ok := t.(*ast.StarExpr); ok {
						t = s.X
					}
					if ix, ok := t.(*ast.IndexExpr); ok {
						t = ix.X
					}
					if ix, ok := t.(*ast.IndexListExpr); ok {
						t = ix.X
					}
					if id, ok := t.(*ast.Ident); ok {
						recv = id.Name
					}
				}
				enc.Encode(map[string]any{"rec": "pdef", "kind": "func", "name": d.Name.Name, "recv": recv,
					"pkgname": file.Name.Name, "file": r, "line": fset.Position(d.Pos()).Line})
			case *ast.GenDecl:
				for _, s := range d.Specs {
					switch s := s.(type) {
					case *ast.TypeSpec:
						enc.Encode(map[string]any{"rec": "pdef", "kind": "type", "name": s.Name.Name,
							"pkgname": file.Name.Name, "file": r, "line": fset.Position(s.Pos()).Line})
					case *ast.ValueSpec:
						for _, n := range s.Names {
							enc.Encode(map[string]any{"rec": "pdef", "kind": strings.ToLower(d.Tok.String()), "name": n.Name,
								"pkgname": file.Name.Name, "file": r, "line": fset.Position(n.Pos()).Line})
						}
					}
				}
			}
		}
		return nil
	})
}

// isLocal reports whether fn belongs to the loaded packages. Bound method
// values and method expressions are synthetic wrappers without a package;
// they count as the method they wrap.
func isLocal(fn *ssa.Function, local map[*types.Package]bool) bool {
	if fn.Pkg != nil {
		return local[fn.Pkg.Pkg]
	}
	if o, ok := fn.Object().(*types.Func); ok && o != nil && o.Pkg() != nil {
		return local[o.Pkg()]
	}
	return false
}
