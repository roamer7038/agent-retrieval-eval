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
| `grade/` | 採点、費用の換算、集計 |

## 使い方

必要なもの: Linux、Docker、Python 3 と PyYAML、git。

```sh
scripts/fetch_corpora.py c1
docker build -t are-proxy:dev docker/proxy
docker build -t are-agent:dev -f docker/agent.Dockerfile docker
harness/run.py run --question pe0-s1 --model local:qwen3.8:27b --label pe0      # 1 セッション
harness/run.py batch --questions all --models local:qwen3.8:27b --label pe0     # まとめて
grade/grade.py ../agent-retrieval-eval-runs/pe0 > scores.jsonl
grade/summarize.py scores.jsonl
```

- モデルは `local:<名前>`（Ollama の Anthropic 互換 API。事前実験だけに使う）か `anthropic:<sonnet|opus|haiku>`（`~/.config/agent-retrieval-eval/oauth-token` の OAuth トークン）。
- 記録は `../agent-retrieval-eval-runs/`（`$ARE_RUNS`）に書く。Claude Code は上位のディレクトリにある Git リポジトリの状態をシステムプロンプトに入れるので、Git リポジトリの外に置く。トランスクリプトを含むので公開しない。
- ローカル LLM の結果の `total_cost_usd` は、未知のモデルに仮の単価を当てた値なので使わない（採点では USD を出さない）。

## ライセンス

MIT。題材のリポジトリはそれぞれのライセンスに従い、このリポジトリには複製しない。
