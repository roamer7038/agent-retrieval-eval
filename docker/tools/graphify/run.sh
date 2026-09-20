#!/bin/bash
# graphify without an LLM: `graphify update <repo>` re-extracts with the local
# tree-sitter pass only (code, plus the links/headings structure of Markdown;
# no semantic pass), clusters, and writes <repo>/graphify-out/ (graph.json,
# GRAPH_REPORT.md, graph.html, cache/). The same command builds the graph from
# nothing (index) and after a change (update); per-file AST results are cached
# in graphify-out/cache, so update re-parses only changed files, then rebuilds
# the graph and the clustering.
set -euo pipefail
case "$1" in
  prepare) exit 0 ;;
  index|update)
    for r in $REPOS; do
      graphify update "/work/$r"
    done ;;
  query)
    for r in $REPOS; do
      echo "## $r"
      graphify query "$Q_SYMBOL" --graph "/work/$r/graphify-out/graph.json" --budget 1000 || true
    done ;;
  *) exit 2 ;;
esac
