# agent-retrieval-eval

Markdown の文書とソースコードを対象に、コーディングエージェントの情報取得の手法（grep などの標準のコマンド、シンボル索引、BM25、埋め込み、グラフ、LSP）が、どの情報取得のシナリオで品質・費用・時間（QCD）の点で有効かを比べる実験の、課題・実行環境・採点のスクリプト。

Experiments comparing retrieval methods and tools for coding agents over Markdown and source code.

## 状態

事前実験の段階。本番の結果はまだない。

## 構成

| パス | 内容 |
|---|---|
| `corpora/corpora.yaml` | 題材のリポジトリと固定したコミット |
| `scripts/fetch_corpora.py` | 題材を `data/corpora/` に取得する |
| `docker/agent.Dockerfile` | セッションを動かすイメージ（Claude Code の版を固定） |
| `docker/proxy/` | セッションから外への唯一の経路。許可したホスト（Claude の API、事前実験のローカル LLM）だけを通す |
| `harness/run.py` | 1 問を 1 セッションとして実行し、記録を残す |
| `tasks/` | 課題文の雛形、条件ごとの環境の説明、問題と正解 |
| `gen/` | 問題と正解の生成（解析器・git・文書の構造から機械で作る正解、ローカル LLM による言い換えと英訳、LLM 判定者の試作） |
| `grade/` | 採点、費用の換算、集計 |
| `docker/tools/`、`scripts/l0.py`、`scripts/l0_table.py` | L0（道具の導入と索引の測定）。道具ごとのイメージ、測定、表 |
| `gen/` | 問題と正解の生成（解析器、候補、言い換えと対訳、LLM 判定） |
| `results/` | 事前実験の結果（`pe1-l0.*` は L0、`pe2-*`・`pe2b.md`・`pe2c.md` は正解の作り方と監査） |

## 使い方

必要なもの: Linux、Docker、Python 3 と PyYAML、git。

```sh
scripts/fetch_corpora.py c1
docker build -t are-proxy:dev docker/proxy
docker build -t are-agent:dev -f docker/agent.Dockerfile docker
harness/run.py run --question pe0-s1 --model local:qwen3.8:27b --label pe0      # 1 セッション
harness/run.py batch --questions all --models local:qwen3.8:27b --label pe0     # まとめて
grade/grade.py ../agent-retrieval-eval-runs/pe0 > scores.jsonl       # 自由記述は --judgments で判定を渡す
grade/summarize.py scores.jsonl

scripts/l0.py build ctags zoekt                                          # L0 の道具のイメージ
scripts/l0.py batch --tools ctags,zoekt --corpora c1,c2,c4,c3 --slot a   # 索引の作成・更新・検索を測る
scripts/l0.py run serena c2 --slot b --variant go+ts --env "SERENA_C2_LANGS=go typescript"
scripts/l0.py run semble c3 --slot a --variant mem32g --memory 32g          # 制限を変えた測定

scripts/l0_table.py                                                      # 道具 × 題材の表
```

- モデルは `local:<名前>`（Ollama の Anthropic 互換 API。事前実験だけに使う）か `anthropic:<sonnet|opus|haiku>`（`~/.config/agent-retrieval-eval/oauth-token` の OAuth トークン）。
- 題材と索引は `data/`（`$ARE_DATA`）に置く。作業ツリーを複数使うときは、同じ `$ARE_DATA` を指して共有する。
- 記録は `../agent-retrieval-eval-runs/`（`$ARE_RUNS`）に書く。Claude Code は上位のディレクトリにある Git リポジトリの状態をシステムプロンプトに入れるので、Git リポジトリの外に置く。トランスクリプトを含むので公開しない。
- L0 の測定は 1 回 8 vCPU・16GB・ネットワークなし、索引の作成と更新はそれぞれ 60 分が上限。計測枠（`--slot`）ごとに同時に 1 つだけ動く（ロックで待つ）。
- ローカル LLM の結果の `total_cost_usd` は、未知のモデルに仮の単価を当てた値なので使わない（採点では USD を出さない）。

## 問題と正解の生成

```sh
ARE_DATA=... gen/analyze.sh all   # Go（go/packages・SSA・VTA）、TypeScript（コンパイラの構文解析）、C（clang でビルドした Linux を libclang で解析）
ARE_DATA=... gen/run.sh dev       # 候補 → S9 の正解の選別 → 言い換え・一意性の判定・英訳 → tasks/dev/pe2.jsonl
grade/selfcheck.py tasks/dev/pe2.jsonl /tmp/gradecheck   # 全問を採点できるかの確認（合成セッション）
```

- 生成物（解析の結果、候補、LLM の応答のキャッシュ）は `$ARE_DATA/gold-work/` に置き、題材の作業ツリーには書かない。Linux は題材を読み取り専用でマウントしたコンテナの中で、別のディレクトリにビルドする。
- 乱数は分割名（`dev`・`test`）と `gen/common.py` の `SEED` で決まり、項目の分割は項目の鍵のハッシュで決まる。本番用は事前登録の後に `gen/run.sh test` で同じ手順で作る。
- 問題の各行は、識別子を含む質問と言い換えの対（`phrasing`）、英訳（`question_en`）、言語の組み合わせ（`lang`）、正解（`gold`）、根拠として認める範囲（`evidence`。末尾が `/` なら配下すべて。何を根拠と認めるかの説明は `evidence_rule`）、正解の状態（`gold_status`: 抜き取りで監査する `machine`、人の確認が要る `needs_review`）、監査の結果（`audit`）を持つ。正解の種類は `grade/grade.py` の冒頭に書いた。
- 監査の記録は `results/pe2-audit.jsonl`。最後の監査で正解の誤り（`error`）か問題として成り立たない（`invalid`）とされた候補は、`gen/build.py` が問題に入れず、生成器も次に引くときに飛ばして別の候補を引く。
- テストとテスト用の代用品を除く規則は `gen/common.py` の `TEST_PATH`（S3・S7 の正解と質問文が同じものを使う）。S2 の「主のファイル」の規則は `gen/cand_git.py` の `main_file`。
- LLM 判定者の試作は `gen/judge.py`（S4・S5 の自由記述の採点と、S9 の正解の選別）。自由記述の採点は別系統のモデル 2 つで判定し、割れたものを `split` として残す。S9 の選別は判定者 1 つ（`gemma4:31b`）とエージェントの裁定で行う（2 人目はこの課題では κ ≈ 0 だった）。結果は `results/pe2-gold.md`（1 回目）、`results/pe2b.md`（作り直し）、`results/pe2c.md`（S4・S9 の作り直し）。エージェントが付けた札は `results/pe2*-judge-human.jsonl`・`results/pe2*-s9-human.jsonl`。

## ライセンス

MIT。題材のリポジトリはそれぞれのライセンスに従い、このリポジトリには複製しない。
