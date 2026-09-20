#!/bin/bash
# wikictl: documents only (c1 wiki, c4 website). The corpus is pinned to a
# detached commit, so a local branch are-l0 is made at that commit and
# wikictl reads it through its bare mirror under $XDG_CACHE_HOME. update
# commits the changed files to are-l0 (wikictl reads commits, not the working
# tree) and fetches.
set -euo pipefail
case "$CORPUS" in
  c1) R=/work/wiki ;;
  c4) R=/work/website ;;
  *) exit 3 ;;
esac
export XDG_CACHE_HOME=/index/cache WIKICTL_CONFIG=/index/wikictl.yaml
export GIT_AUTHOR_NAME=l0 GIT_AUTHOR_EMAIL=l0@example.invalid GIT_COMMITTER_NAME=l0 GIT_COMMITTER_EMAIL=l0@example.invalid
case "$1" in
  prepare)
    git -C "$R" branch -f are-l0 HEAD
    printf 'repo: %s\nbranch: are-l0\n' "$R" > "$WIKICTL_CONFIG" ;;
  index) wikictl ls > /dev/null ;;
  update)
    git -C "$R" checkout -q are-l0
    git -C "$R" commit -qam "L0 update test"
    wikictl ls > /dev/null ;;
  query)
    wikictl grep -n -F -- "$Q_SYMBOL" | head -20 || true
    wikictl grep -n -i -F -- "$Q_TEXT" | head -20 || true ;;
  *) exit 2 ;;
esac
