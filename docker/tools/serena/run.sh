#!/bin/bash
# Serena: symbols of every source file cached through the language servers
# (`serena project index`), per repository. The project configuration and the
# caches live in /work/<repo>/.serena, Serena's global configuration in
# $HOME/.serena. update runs the same command again: files whose content hash
# is unchanged come from the cache, only changed files go to the language
# server (but every file is hashed and the servers start again).
set -euo pipefail

# Language servers per repository; empty = no code for Serena (skipped).
langs() {
  case "$CORPUS/$1" in
    c1/wikictl) echo "go" ;;
    # TypeScript is left out by default: through Serena, tsserver took about
    # 1-1.5 s per file on grafana (9,425 files, i.e. hours); set
    # SERENA_C2_LANGS="go typescript" to include it.
    c2/grafana) echo "${SERENA_C2_LANGS:-go}" ;;
    c3/linux) echo "cpp" ;;
    *) echo "" ;;
  esac
}

setup() {
  mkdir -p "$HOME/.serena"
  [ -f "$HOME/.serena/serena_config.yml" ] || cp /opt/l0/serena_config.yml "$HOME/.serena/serena_config.yml"
  # Go's telemetry (local counters by default, used by gopls) off.
  go telemetry off 2>/dev/null || true
}

index_repo() {
  local r=$1 args=()
  for l in $(langs "$r"); do args+=(--ls "$l"); done
  serena project index "/work/$r" "${args[@]}" --log-level INFO --timeout "${SERENA_FILE_TIMEOUT:-60}"
}

prepare_linux() {
  # compile_commands.json from a real build of defconfig (in the tree).
  cd /work/linux
  make -s defconfig
  make -s -j"$(nproc)"
  python3 scripts/clang-tools/gen_compile_commands.py
  ls -l compile_commands.json
}

[ "$CORPUS" = c4 ] && exit 3
setup
case "$1" in
  prepare)
    if [ "$CORPUS" = c3 ]; then prepare_linux; fi ;;
  index|update)
    for r in $REPOS; do
      [ -n "$(langs "$r")" ] || { echo "skip $r (no code for Serena)" >&2; continue; }
      index_repo "$r"
    done ;;
  query)
    for r in $REPOS; do
      [ -n "$(langs "$r")" ] || continue
      python3 /opt/l0/query.py "/work/$r" "$Q_SYMBOL" || echo "query failed for $r" >&2
    done ;;
  *) exit 2 ;;
esac
