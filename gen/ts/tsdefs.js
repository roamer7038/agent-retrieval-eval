#!/usr/bin/env node
// Top-level declarations of every .ts/.tsx file under the given directories,
// from the TypeScript compiler's parser (no type checking, so no
// node_modules are needed). The gold of S1 and S7 for the TypeScript part of
// the Grafana corpus.
//
//   NODE_PATH=<dir with typescript> node tsdefs.js <repo root> <out.jsonl> <dir>...
//
// {"rec":"def","kind":"function|class|interface|type|enum|const","name",
//  "file","line","end","exported","default","doc","test","jsx"}
const fs = require('fs');
const path = require('path');
const ts = require('typescript');

const [root, outPath, ...dirs] = process.argv.slice(2);
const out = fs.createWriteStream(outPath);

function walk(d, files) {
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    if (e.name === 'node_modules' || e.name === '.git' || e.name === 'dist') continue;
    const p = path.join(d, e.name);
    if (e.isDirectory()) walk(p, files);
    else if (/\.(ts|tsx)$/.test(e.name) && !e.name.endsWith('.d.ts')) files.push(p);
  }
  return files;
}

function doc(node, sf) {
  const jsdoc = ts.getJSDocCommentsAndTags(node).filter(ts.isJSDoc);
  if (jsdoc.length) {
    const c = jsdoc[jsdoc.length - 1].comment;
    const s = typeof c === 'string' ? c : Array.isArray(c) ? c.map((x) => x.text || '').join('') : '';
    return s.replace(/\s+/g, ' ').trim().slice(0, 400);
  }
  return '';
}

function mods(node) {
  const m = ts.canHaveModifiers(node) ? ts.getModifiers(node) || [] : [];
  return {
    exported: m.some((x) => x.kind === ts.SyntaxKind.ExportKeyword),
    def: m.some((x) => x.kind === ts.SyntaxKind.DefaultKeyword),
  };
}

for (const dir of dirs) {
  for (const file of walk(path.join(root, dir), [])) {
    const rel = path.relative(root, file);
    const text = fs.readFileSync(file, 'utf8');
    const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true,
      file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
    const test = /\.(test|spec)\.tsx?$/.test(file) || /__(tests|mocks)__/.test(rel);
    const put = (kind, nameNode, node, extra = {}) => {
      const { line } = sf.getLineAndCharacterOfPosition(node.getStart(sf));
      const end = sf.getLineAndCharacterOfPosition(node.getEnd()).line;
      out.write(JSON.stringify({ rec: 'def', kind, name: nameNode.text, file: rel, line: line + 1, end: end + 1,
        ...mods(node), doc: doc(node, sf), test, ...extra }) + '\n');
    };
    for (const st of sf.statements) {
      if (ts.isFunctionDeclaration(st) && st.name) put('function', st.name, st);
      else if (ts.isClassDeclaration(st) && st.name) put('class', st.name, st);
      else if (ts.isInterfaceDeclaration(st)) put('interface', st.name, st);
      else if (ts.isTypeAliasDeclaration(st)) put('type', st.name, st);
      else if (ts.isEnumDeclaration(st)) put('enum', st.name, st);
      else if (ts.isVariableStatement(st)) {
        const m = mods(st);
        for (const d of st.declarationList.declarations) {
          if (!ts.isIdentifier(d.name)) continue;
          const init = d.initializer;
          const fn = init && (ts.isArrowFunction(init) || ts.isFunctionExpression(init));
          const { line } = sf.getLineAndCharacterOfPosition(st.getStart(sf));
          const end = sf.getLineAndCharacterOfPosition(st.getEnd()).line;
          out.write(JSON.stringify({ rec: 'def', kind: fn ? 'const-function' : 'const', name: d.name.text, file: rel,
            line: line + 1, end: end + 1, exported: m.exported, def: false, doc: doc(st, sf), test }) + '\n');
        }
      }
    }
  }
}
out.end();
