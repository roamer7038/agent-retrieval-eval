#!/bin/bash
# GitNexus: `gitnexus analyze --index-only` per repository. The index is
# <repo>/.gitnexus/ (LadybugDB) and the repository is registered in
# ~/.gitnexus/registry.json (HOME=/index/home). --index-only keeps analyze
# from writing AGENTS.md, CLAUDE.md and .claude/skills/ into the repository.
# Embeddings are off by default and stay off unless L0_EMBEDDINGS=1 (then
# analyze --embeddings with the model baked into the image, HF_HOME=/opt/hf).
# update runs analyze again: it detects the changed files and replays cached
# parser output for the rest.
set -euo pipefail
emb=()
[ "${L0_EMBEDDINGS:-0}" = 1 ] && emb=(--embeddings)
mkdir -p "$HOME" && ln -sfn /opt/lbdb/.lbdb "$HOME/.lbdb"
case "$1" in
  prepare) exit 0 ;;
  index|update)
    for r in $REPOS; do
      gitnexus analyze --index-only "${emb[@]}" --name "$r" "/work/$r"
    done ;;
  query)
    for r in $REPOS; do
      echo "## $r"
      gitnexus query "$Q_SYMBOL" -r "$r" || true
    done ;;
  *) exit 2 ;;
esac
