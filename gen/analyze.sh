#!/bin/sh
# Compiler-grade analyses of the code corpora, the source of the machine gold
# (S1, S3, S7, and the code side of S9). Writes $ARE_DATA/gold-work/out and
# never writes into the corpora (the Linux build is out of tree, with the
# source mounted read-only).
#
#   ARE_DATA=... gen/analyze.sh [c1|c2|c3|all]
#
# Needs: Go (the module and build caches go to gold-work/go), Node, Docker.
set -eu
: "${ARE_DATA:?set ARE_DATA}"
GEN=$(cd "$(dirname "$0")" && pwd)
W=$ARE_DATA/gold-work
C=$ARE_DATA/corpora
mkdir -p "$W/out" "$W/tools" "$W/logs"
export GOPATH=$W/go GOMODCACHE=$W/go/pkg/mod GOCACHE=$W/go/cache GOTOOLCHAIN=auto
what=${1:-all}

(cd "$GEN/goanalyze" && go build -o "$W/tools/goanalyze" .)

if [ "$what" = c1 ] || [ "$what" = all ]; then
  "$W/tools/goanalyze" -dir "$C/c1/wikictl" -out "$W/out/c1-go.jsonl" -cha -vta -tests ./...
  "$W/tools/goanalyze" -parse-only -dir "$C/c1/wikictl" -out "$W/out/c1-go-parse.jsonl"
fi

if [ "$what" = c2 ] || [ "$what" = all ]; then
  # the root module's packages; nested modules (go.work) are covered by -parse-only
  (cd "$C/c2/grafana" && go mod download)
  "$W/tools/goanalyze" -dir "$C/c2/grafana" -out "$W/out/c2-go.jsonl" -vta ./pkg/...
  "$W/tools/goanalyze" -parse-only -dir "$C/c2/grafana" -out "$W/out/c2-go-parse.jsonl"
  [ -d "$W/tools/node/node_modules/typescript" ] ||
    (mkdir -p "$W/tools/node" && cd "$W/tools/node" && npm init -y >/dev/null &&
     npm install --no-audit --no-fund --cache "$W/tools/npm-cache" typescript@5.9.3)
  NODE_PATH=$W/tools/node/node_modules node "$GEN/ts/tsdefs.js" "$C/c2/grafana" "$W/out/c2-ts.jsonl" public packages apps
fi

if [ "$what" = c3 ] || [ "$what" = all ]; then
  docker build -q -t are-gold-clang:dev "$GEN/c" >/dev/null
  mkdir -p "$W/linux-build"
  run() {
    docker run --rm -u "$(id -u):$(id -g)" -v "$C/c3/linux:/src:ro" -v "$W/linux-build:/build" \
      -v "$GEN/c:/gen:ro" -w /src are-gold-clang:dev sh -c "$1"
  }
  run 'make O=/build LLVM=1 defconfig >/dev/null && make O=/build LLVM=1 -j"$(nproc)" vmlinux modules >/build/build.log 2>&1 &&
       python3 scripts/clang-tools/gen_compile_commands.py -d /build -o /build/compile_commands.json'
  run 'python3 /gen/canalyze.py --src /src --db /build/compile_commands.json --out /build/c3-c.jsonl --jobs "$(nproc)"'
  mv "$W/linux-build/c3-c.jsonl" "$W/out/c3-c.jsonl"
  rm -rf "$W/linux-build/c3-c.jsonl.parts"
fi
