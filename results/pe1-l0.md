# PE1: 道具の導入と索引の測定（L0）

事前実験 PE1 の結果。道具 × 題材で索引を作り、作成の時間・容量・メモリ・失敗・差分更新・1 回の検索の時間を測った。測定の記録は [`pe1-l0.jsonl`](pe1-l0.jsonl)、表は `scripts/l0_table.py` で作る。事実（測った値）と解釈（そこから何を読むか）は節を分けて書く。

## 1. 測り方

### 計測環境

| 項目 | 値 |
|---|---|
| ホスト | koishi（WSL2、kernel 6.18.33.2-microsoft-standard-WSL2）、Docker 29.8.0 |
| CPU | Intel Core i7-13700K（24 スレッド、P コアと E コアの混在） |
| メモリ | 62GiB（swap は計測に使わない） |
| GPU | NVIDIA RTX 5070 Ti 16GiB（driver 616.56）。qmd の埋め込みだけが使う |
| 1 回の制限 | 8 vCPU（`--cpuset-cpus`）、メモリ 16GiB（`--memory-swap` も 16GiB）、ネットワークなし |
| 上限 | 索引の作成・差分更新はそれぞれ 60 分、検索は 10 分で打ち切る |

計測枠（slot）は vCPU の集合（a=0-7、b=8-15、c=16-23、GPU は c）。**1 つの枠では同時に 1 つの測定だけを動かす**（`$ARE_DATA/indexes/.slot-<枠>.lock` の排他ロックで待つ）。枠をまたいだ同時実行は最大 2 本まで行った。各記録には、開始時に他の枠で動いていた測定（`others_running_at_start`）と、開始・終了時のホストの負荷（`loadavg_start`・`loadavg_end`）を残している。

限界: WSL2 の vCPU は Hyper-V が物理コアへ割り当てるため、枠は物理コア（P コア・E コア）に固定されない。枠をまたいだ 2 本の同時実行では、記憶帯域とディスクを共有する。したがって時間は「この環境での目安」であり、道具の間の大小を比べるためのもので、絶対値の再現性は主張しない。

### 手順

`scripts/l0.py run <道具> <題材>` が 1 行の記録を作る。

1. 題材を overlay で `/work` に重ねる（道具は木に書き込めるが、題材のディレクトリは変わらない）。索引の置き場所は `/index`、`HOME` も `/index/home`。
2. `prepare`: 索引そのものではない準備（Serena の c3 では defconfig のビルドと `compile_commands.json`）。当てはまらなければ何もしない。
3. `index`: 何もない状態から索引を作る。
4. `query`: 題材ごとに決めた識別子（`Q_SYMBOL`）と語句（`Q_TEXT`）で 1 回検索し、結果が出るかを見る。
5. コードの 1 ファイルに関数 `areL0Probe` を、文書の 1 ファイルに `areL0Probe` を含む段落を追加する。
6. `update`: 道具の差分更新のコマンドを走らせ、もう一度 `areL0Probe` を検索して、追加が索引に入ったか（「更新の反映」）を見る。

道具が題材に当てはまらないときは `exit 3`（表の `n/a`。cscope は C だけ、qmd は Markdown だけ、wikictl は文書の題材だけ、Serena は言語サーバのある言語だけ）。

測る量の定義:

- **索引 s**: `index` の実時間。**索引 CPU s** は同じ区間の cgroup の CPU 時間（8 vCPU なので実時間の最大 8 倍になりうる）。
- **容量 MiB**: 索引の作成で増えた分（木への書き込み＋`/index`。準備で書いたものと計測のログは引く）。
- **メモリ MiB**: コンテナの無名メモリの最大（0.2 秒ごとの標本）。準備と索引の大きい方。
- **検索 s**: `query` の実時間。コンテナの起動と道具の立ち上げを含む（常駐させれば L1 ではこれより短くなりうる）。
- **更新 s**: `update` の実時間。

### 題材

| 題材 | 内容 | ファイル数 | うち .md | 大きさ |
|---|---|--:|--:|--:|
| c1 | wikictl（Go）＋ wiki（日本語 Markdown） | 209 | 137 | 2MB |
| c2 | grafana（Go・TypeScript）＋ docs | 23,357 | 1,066 | 281MB |
| c3 | linux（C）＋ Documentation（reStructuredText） | 95,936 | 9 | 1,809MB |
| c4 | kubernetes/website（Markdown、多言語） | 13,088 | 8,233 | 534MB |

（`.git` を除いた実際の作業ツリー。固定コミットは `corpora/corpora.yaml`）

## 2. 事実: 道具 × 題材

15 の道具 × 4 つの題材（当てはまらない組み合わせを含む）と、設定を変えた 3 件を合わせて 63 件。結果は ok 48・n/a 8（道具が題材に当てはまらない）・timeout 4（60 分で索引が終わらない）・failed 3（メモリ不足など）。

表は `scripts/l0_table.py` の出力（道具と題材ごとの最後の記録）。**この表の値はすべて 2026-09-20 04:39 以降の、枠を占有した測り直しの記録**。前の測り方（同じ時間に他の測定が動いていた 35 件）も `pe1-l0.jsonl` に残してある。

| 系統 | 道具 | 題材 | 結果 | slot | 準備 s | 索引 s | 索引 CPU s | 容量 MiB | メモリ MiB | 更新 s | 更新の反映 | 検索 s | 検索の一致 |
|---|---|---|---|---|--:|--:|--:|--:|--:|--:|---|--:|---|
| シンボル索引 | ctags | c1 | ok | a |  | 0.03 | 0.02 | 0.43 | 12 | 0.03 | あり | 0.00 | あり |
| シンボル索引 | ctags | c2 | ok | a |  | 10.4 | 9.16 | 572 | 912 | 6.3 | あり | 0.00 | あり |
| シンボル索引 | ctags | c3 | ok | a |  | 61.6 | 64.67 | 1,909 | 3,290 | 54.2 | あり | 0.00 | あり |
| シンボル索引 | ctags | c4 | ok | a |  | 4.1 | 2.37 | 111 | 172 | 1.8 | なし | 0.00 | なし |
| シンボル索引 | global | c1 | ok | a |  | 0.11 | 0.06 | 0.23 | 12 | 0.06 | あり | 0.02 | あり |
| シンボル索引 | global | c2 | ok | a |  | 304 | 332.73 | 14,548 | 87 | 15.5 | あり | 0.01 | あり |
| シンボル索引 | global | c3 | ok | a |  | 27.3 | 30.7 | 1,584 | 91 | 3.6 | あり | 0.01 | あり |
| シンボル索引 | global | c4 | ok | a |  | 71.0 | 80.53 | 3,525 | 88 | 2.1 | なし | 0.01 | なし |
| シンボル索引 | cscope | c1 | n/a | a |  |  |  |  |  |  |  |  |  |
| シンボル索引 | cscope | c2 | n/a | a |  |  |  |  |  |  |  |  |  |
| シンボル索引 | cscope | c3 | ok | a |  | 62.5 | 96.5 | 2,490 | 7,270 | 50.0 | あり | 0.02 | あり |
| シンボル索引 | cscope | c4 | n/a | a |  |  |  |  |  |  |  |  |  |
| 構文・字句 | ast-grep | c1 | ok | a |  | 0.00 | 0.01 | 0.00 | 12 | 0.00 | あり | 0.07 | あり |
| 構文・字句 | ast-grep | c2 | ok | a |  | 0.00 | 0.0 | 0.00 | 12 | 0.00 | あり | 2.1 | あり |
| 構文・字句 | ast-grep | c3 | ok | a |  | 0.00 | 0.0 | 0.00 | 12 | 0.00 | あり | 5.4 | なし |
| 構文・字句 | ast-grep | c4 | n/a | a |  |  |  |  |  |  |  |  |  |
| 構文・字句 | probe | c1 | ok | a |  | 0.00 | 0.0 | 0.00 | 12 | 0.00 | あり | 0.41 | あり |
| 構文・字句 | probe | c2 | ok | a |  | 0.00 | 0.01 | 0.00 | 12 | 0.00 | あり | 6.7 | なし |
| 構文・字句 | probe | c3 | ok | a |  | 0.00 | 0.0 | 0.00 | 12 | 0.00 | なし | 38.6 | なし |
| 構文・字句 | probe | c4 | ok | a |  | 0.00 | 0.0 | 0.00 | 12 | 0.00 | なし | 6.5 | あり |
| 構文・字句 | zoekt | c1 | ok | a |  | 0.32 | 0.39 | 4 | 79 | 0.31 | あり | 0.16 | あり |
| 構文・字句 | zoekt | c2 | ok | a |  | 10.7 | 20.01 | 503 | 1,129 | 10.7 | あり | 0.21 | あり |
| 構文・字句 | zoekt | c3 | ok | a |  | 102 | 226.52 | 3,339 | 1,255 | 108 | あり | 0.32 | あり |
| 構文・字句 | zoekt | c4 | ok | a |  | 9.2 | 11.04 | 300 | 769 | 8.8 | あり | 0.31 | あり |
| BM25・混合 | qmd | c1 | ok | c |  | 14.2 | 22.04 | 6 | 1,777 | 2.5 | あり | 4.2 | なし |
| BM25・混合 | qmd | c2 | ok | c |  | 143 | 240.18 | 46 | 2,054 | 2.9 | あり | 4.2 | なし |
| BM25・混合 | qmd | c3 | n/a | c |  |  |  |  |  |  |  |  |  |
| BM25・混合 | qmd | c4 | ok | c |  | 1,242 | 2049.08 | 354 | 2,106 | 4.1 | あり | 4.6 | あり |
| BM25・混合 | semble | c1 | ok | a |  | 2.7 | 3.93 | 4 | 147 | 1.9 | あり | 0.97 | あり |
| BM25・混合 | semble | c2 | ok | a |  | 79.6 | 127.01 | 470 | 2,012 | 14.1 | あり | 10.8 | あり |
| BM25・混合 | semble | c3 | failed（index） | a |  | 644 | 1038.14 |  | 16,369 |  |  |  |  |
| BM25・混合 | semble | c4 | ok | a |  | 5.1 | 7.49 | 19 | 232 | 2.1 | なし | 2.1 | あり |
| BM25・混合 | semble（mem32g） | c3 | ok | a |  | 622 | 1009.85 | 4,058 | 17,464 | 108 | あり | 94.1 | あり |
| BM25・混合 | ck | c1 | ok | b |  | 77.5 | 320.39 | 3 | 255 | 0.51 | あり | 0.71 | あり |
| BM25・混合 | ck | c2 | timeout（index） | b |  | 3,600 | 14888.13 |  | 251 |  |  |  |  |
| BM25・混合 | ck | c3 | timeout（index） | b |  | 3,600 | 14585.83 |  | 274 |  |  |  |  |
| BM25・混合 | ck | c4 | timeout（index） | b |  | 3,600 | 16598.62 |  | 256 |  |  |  |  |
| グラフ | codebase-memory-mcp | c1 | ok | a |  | 10.8 | 12.6 | 10 | 81 | 10.4 | あり | 7.4 | あり |
| グラフ | codebase-memory-mcp | c2 | ok | a |  | 59.0 | 232.39 | 1,217 | 3,772 | 62.5 | あり | 4.0 | あり |
| グラフ | codebase-memory-mcp | c3 | failed（index） | a |  | 193 | 1306.67 |  | 5,027 |  |  |  |  |
| グラフ | codebase-memory-mcp | c4 | ok | a |  | 14.7 | 56.12 | 121 | 740 | 6.2 | なし | 3.7 | なし |
| グラフ | codebase-memory-mcp（mem12g） | c3 | ok | a |  | 857 | 2778.0 | 12,376 | 15,343 | 904 | あり | 7.8 | あり |
| グラフ | codegraph | c1 | ok | a |  | 1.1 | 2.82 | 5 | 283 | 0.56 | あり | 0.31 | あり |
| グラフ | codegraph | c2 | ok | a |  | 57.8 | 209.17 | 1,004 | 4,979 | 1.7 | あり | 0.26 | あり |
| グラフ | codegraph | c3 | ok | a |  | 583 | 1371.13 | 4,769 | 8,475 | 8.0 | あり | 0.82 | あり |
| グラフ | codegraph | c4 | ok | a |  | 1.9 | 4.95 | 8 | 367 | 0.52 | なし | 0.26 | なし |
| グラフ | graphify | c1 | ok | b |  | 2.4 | 3.34 | 7 | 139 | 2.0 | あり | 0.41 | あり |
| グラフ | graphify | c2 | ok | b |  | 388 | 622.34 | 496 | 4,327 | 473 | あり | 9.0 | あり |
| グラフ | graphify | c3 | ok | b |  | 1,694 | 2739.43 | 3,429 | 11,228 | 366 | なし | 0.21 | なし |
| グラフ | graphify | c4 | ok | b |  | 48.6 | 72.23 | 148 | 524 | 65.8 | なし | 3.4 | あり |
| グラフ | gitnexus | c1 | ok | a |  | 24.7 | 30.56 | 80 | 1,829 | 17.6 | あり | 1.2 | あり |
| グラフ | gitnexus | c2 | ok | a |  | 403 | 1321.11 | 3,528 | 10,121 | 335 | なし | 1.1 | あり |
| グラフ | gitnexus | c3 | failed（index） | a |  | 895 | 5056.59 |  | 16,010 |  |  |  |  |
| グラフ | gitnexus | c4 | ok | a |  | 35.9 | 55.04 | 455 | 2,679 | 10.4 | なし | 0.67 | なし |
| LSP | serena | c1 | ok | a |  | 7.1 | 12.05 | 4 | 393 | 4.3 | あり | 5.6 | あり |
| LSP | serena | c2 | ok | a |  | 1,140 | 3531.38 | 491 | 6,364 | 15.1 | あり | 23.8 | あり |
| LSP | serena | c3 | timeout（index） | a | 278 | 3,600 | 8354.62 |  | 4,138 |  |  |  |  |
| LSP | serena | c4 | n/a | a |  |  |  |  |  |  |  |  |  |
| LSP | serena（go+ts） | c2 | ok | a |  | 1,039 | 3125.9 | 720 | 11,334 | 28.2 | あり | 41.8 | あり |
| 自作 | wikictl | c1 | ok | a |  | 0.16 | 0.11 | 0.52 | 12 | 0.06 | あり | 0.06 | なし |
| 自作 | wikictl | c2 | n/a | a |  |  |  |  |  |  |  |  |  |
| 自作 | wikictl | c3 | n/a | a |  |  |  |  |  |  |  |  |  |
| 自作 | wikictl | c4 | ok | a |  | 13.6 | 25.86 | 530 | 738 | 1.0 | あり | 1.2 | あり |

「更新の反映」は、コードの 1 ファイルに追加した `areL0Probe` が、差分更新のあとの検索で見つかったか。「検索の一致」は最初の検索の出力に `Q_SYMBOL` が現れたかで、道具の質ではなく健全性の確認である。「なし」には次の理由が混ざっている: 道具が語句（`Q_TEXT`）だけで検索する（qmd）、出力がパスと行だけで本文を出さない（probe）、題材にその識別子が無い（c4 のコードのシンボル、wikictl が見る wiki）。

表に現れない事実:

- **測り直しで時間が縮んだ**。同じ組み合わせで、Serena c2 は 1,575→1,140 秒、GNU global c2 は 436→304 秒、ck c1 は 159→77 秒。前の測定では同じ時間に他の測定が動いていた。
- **Serena の TypeScript は 3 時間ではなかった**。枠を占有して測ると、c2 は Go のみ 1,140 秒に対し Go＋TypeScript が 1,039 秒（16,185 ファイル、索引の進捗は 17 分 55 秒）で終わった。容量は 491→720MiB、メモリは 6.4→11.3GiB、1 回の検索は 23.8→41.8 秒に増える。前任の「1 ファイル 1〜1.5 秒で 3 時間以上」という見積もりは、重なりのある測定でのもので、再現しなかった。
- **Serena の c3 は 60 分では終わらない**。準備（defconfig のビルドと `compile_commands.json` 3,093 件）は 278 秒で成功。索引は 48 分で 65,824 ファイル中 4,242 件（1.47 ファイル/秒）＝全体で約 12 時間の見込み（推測）。統括の決定どおり、時間切れ（timeout）として記録した。
- **ck はどの中〜大規模の題材でも 60 分で終わらない**。60 分で索引できたファイル数は c2 が 4,891/23,357、c3 が 3,568/95,936、c4 が 28/13,088。c4 では生成された Kubernetes API リファレンス（`static/docs/reference/generated`、85MB）に時間を使っていた。ck が作る既定の `.ckignore` は、この種の生成物を除かない。
- **codebase-memory-mcp は道具側のメモリ予算で止まる**。c3 は既定の予算 4GiB で `over_memory_budget` として失敗する（コンテナの 16GiB には届いていない）。`CBM_MEM_BUDGET_MB=12288` を与えると成功した（索引 857 秒、容量 12.4GiB、更新 904 秒）。
- **semble と GitNexus は 16GiB の制限で c3 が OOM**（semble は 644 秒で、GitNexus は 895 秒で、いずれも `oom_kill=1`）。semble はコンテナの制限を 32GiB にすると成功した（索引 622 秒、無名メモリの最大 17.5GiB、検索 94 秒）。
- **graphify の c3 は索引はできるが、既定では検索も更新もできない**。`graph.json` が 1.72GB になり、道具の既定の上限 512MB を超えるため `query` と `update` が拒否される。`GRAPHIFY_MAX_GRAPH_BYTES=4GB` を与えて手で検索したところ、1,061,267 ノードのグラフから `copy_process()` を起点とする近傍が返った（この確認は jsonl に記録していない）。
- **GitNexus の c2 は、更新のあとの検索に `areL0Probe` が現れなかった**（更新そのものは 335 秒で正常終了）。索引に入らなかったのか、検索が拾わなかったのかは切り分けていない。
- 道具がエージェントの設定を書き込む件: GitNexus は `analyze --index-only` を付けないと `AGENTS.md`・`CLAUDE.md`・`.claude/skills/` をリポジトリに書き込む。codegraph・codebase-memory-mcp・Serena は、それぞれ `install`・`install`・`setup` を実行したときだけ書き込む（実行していない）。テレメトリは環境変数で止めた（`CODEGRAPH_TELEMETRY=0`、`DO_NOT_TRACK=1`、`SERENA_USAGE_REPORTING=false`、`HF_HUB_DISABLE_TELEMETRY=1`、`SCARF_ANALYTICS=false`、`go telemetry off`）。

## 3. 解釈

- **系統によって索引の費用が桁で違う**。c3（95,936 ファイル、1.8GB）を基準にすると、シンボル索引と字句は 30〜230 CPU 秒、グラフは 1,400〜2,800 CPU 秒、埋め込み（semble、32GiB）は 4,100 CPU 秒、LSP は 60 分でも終わらない。L2 で「索引の費用を償却した費用」を出すとき、この差が損益分岐を決める。
- **メモリは時間より先に壁になる**。16GiB で落ちたのは c3 の semble・GitNexus・codebase-memory-mcp（道具の予算）。時間の上限（60 分）に当たったのは ck と Serena。L1・L2 の実行環境のメモリを決めるときは、題材の中で最大の道具に合わせる必要がある（c3 で混合系を使うなら 32GiB 以上）。
- **差分更新が効く道具と、作り直しに近い道具がある**。1 ファイルの変更に対し、c3 では codegraph が 8 秒（索引 583 秒）、cbm（12GiB）が 904 秒（索引 857 秒）、ctags が 54 秒（索引 62 秒）、zoekt が 108 秒（索引 102 秒）。「索引を持つ道具は更新が安い」とは言えない。L2 でセッションごとに題材へ書き込みが入る設計にすると、道具によっては更新の費用が無視できない。
- **索引を持たない道具（ast-grep・probe）は、索引の費用が 0 の代わりに 1 回の検索が規模に比例する**（c3 で 5.4 秒・38.6 秒）。L2 では、エージェントが何度も検索することを踏まえると、この差は 1 問あたりの時間に直接効く。
- **Serena は問い合わせが重い**。1 回の検索が c2 で 23.8 秒（Go＋TS では 41.8 秒）。これは言語サーバの起動を含む値で、MCP で常駐させれば下がる。L1・L2 で Serena を扱うときは常駐が前提（下の L1 のメモ）。
- **文書中心の題材では、コードのグラフ・シンボル索引は働かない**。c4 で「更新の反映」が「なし」になった道具（ctags・global・cbm・codegraph・gitnexus）は、Markdown の段落を索引しないため。これは仕様どおりで、L1 の不完全な設計（道具 × 題材の当てはまり）として分析計画に書く必要がある。
- **qmd の検索は言語をまたぐ**。c4 の英語の問い合わせに対し、上位に中国語訳のページが並んだ。多言語の題材（RQ3）では、「同じ内容の別の言語のページ」を正解に含めるかを決めないと、埋め込み系が不当に低く（または高く）出る。

## 4. PE1 の進む条件

条件は「題材ごとに、各系統で 1 つ以上の道具が動く」。

| 題材 | シンボル索引 | 構文・字句 | BM25・混合 | グラフ | LSP | 自作 |
|---|---|---|---|---|---|---|
| c1 | ○ ctags・global | ○ ast-grep・probe・zoekt | ○ qmd・semble・ck | ○ 4 つとも | ○ Serena | ○ wikictl |
| c2 | ○ ctags・global | ○ ast-grep・probe・zoekt | ○ semble・qmd（Markdown） | ○ 4 つとも | ○ Serena（Go、Go＋TS） | ― 対象外 |
| c3 | ○ ctags・global・cscope | ○ ast-grep・probe・zoekt | △ **16GiB では 0**。32GiB なら semble | ○ codegraph（cbm は予算 12GiB、graphify は上限の引き上げが要る、GitNexus は不可） | × Serena は約 12 時間の見込み | ― 対象外 |
| c4 | ○ ctags・global | ○ probe・zoekt | ○ qmd・semble | ○ 4 つとも | ― 対象外（言語サーバの対象なし） | ○ wikictl |

**c1・c2・c4 は条件を満たす。c3 だけが満たさない**（BM25・混合が既定の制限で 0、LSP が時間内に終わらない）。c3 は計画で L0・L1 だけの題材である。次のいずれかを決める必要がある（**統括の判断**）:

1. c3 の L1 ではメモリの上限を 32GiB に上げ、semble を混合系の代表にする（測定済み: 索引 622 秒、検索 94 秒、容量 4.0GiB）。L2 では c3 を使わないので、セッションのコンテナの大きさには影響しない。
2. c3 を混合系・LSP の比較から外し、分析計画の「推定できる対比」にその旨を書く。
3. グラフ系の c3 は codegraph を代表とし、cbm は `CBM_MEM_BUDGET_MB=12288`、graphify は `GRAPHIFY_MAX_GRAPH_BYTES=4GB` という設定を事前登録に書く（道具の既定から外れた設定を使うことを明示する）。

推奨は 1＋2＋3 の併用（semble は 32GiB で入れる、Serena は c3 では扱わない、グラフ系は設定を明記する）。なお 2 の「LSP を c3 から外す」は、計画で S1・S3 の Serena を比較から外していることとも整合する。

## 5. 題材の候補を入れるか

判断の基準は計画のとおり（固定コミットで再現できる、ライセンスが研究利用と正解の公開を許す、主要な道具の索引が現実的な時間で作れる、シナリオの正解を作れる）。これに「既存の C1〜C4 が覆っていない軸を足すか」を加えて見た。規模は 2026-09-20 に GitHub の API で数えた既定ブランチの先頭の値。

| 候補 | 規模（ファイル数／.md） | ライセンス | 正解の作りやすさ | 推奨 |
|---|---|---|---|---|
| gitlabhq/gitlabhq | 54,593 以上（API の木が打ち切られる）／.md 8,185。2.9GB | コードは MIT Expat、`doc/` は CC BY-SA 4.0、`ee/` は無い（FOSS 版） | Ruby 中心（.rb 17,432）。PE2 の機械の正解は gopls・clang・TS コンパイラで作っており、Ruby 用の解析器を足す必要がある | **入れない** |
| vercel/next.js | 32,704／.md 566・.mdx 746。155MB | MIT（文書も同じ） | TypeScript は C2 と同じ仕組みで作れる。ただし文書が .mdx で、qmd・wikictl の既定の対象（`**/*.md`）から外れる | **入れない**（TS が主の題材を足すなら第一候補） |
| nodejs/node | 51,828／.md 682。702MB | 本体は MIT。`deps/`（V8・OpenSSL など）を含み、LICENSE は第三者分を集約したもの | C は C3 が覆う。`deps/` を除く前処理が要る | **入れない** |
| ContextBench Lite（`contextbench_verified` 500 件） | 500 事例。python 266・typescript 66・javascript 60・go 40・c 23・rust 20・java 15・cpp 10。リポジトリ 59（django 80、mui/material-ui 45、transformers 34、cli/cli 32 …） | 評価コード（EuniAI/ContextBench）は Apache-2.0。**データセット（Hugging Face）はカードにライセンスの記載がない** | 人手の正解がパスと行範囲（span）付きであり、S2（振る舞いの実装箇所）に相当する。事例ごとに base commit が違うので、索引系の道具は事例ごとに索引を作る必要がある | **主の題材には入れない**。S2 の外部照合として Go の cli/cli の 32 事例だけを L1 で使う案は残す（**判断が必要**） |

理由の要点:

- 規模を増やす利得が小さい。C2 は「コードと文書が同じリポジトリ」、C3 は「規模の上限」、C4 は「文書中心と多言語」を既に担っている。GitLab・Node は同じ役割を重ねるだけで、索引の費用（下の表のとおり、c3 では 16GiB でグラフ系が落ちる）と PE2 の正解の工数だけが増える。
- 言語が増えると正解の作り方が増える。PE2 の機械の正解は Go・C・TypeScript の解析器で作っており、Ruby・Python を足すと別の解析器と監査が必要になる。
- ContextBench Lite だけは性質が違い、**人手の正解による外部照合**という他で代えられない利点がある。ただし主の題材にすると、事例ごとの固定コミット（59 リポジトリ × 500 事例）に対して索引を作ることになり、L0 の測定からは 1 事例あたり数分〜数十分の索引が要ると見込まれる（推測）。L1 だけ・Go の cli/cli だけなら現実的で、S2 の自作の正解と人手の正解の一致を見られる。データセットのライセンス表示がないため、正解そのものは再配布せず、事例 ID と自分たちの測定値だけを公開する前提とする。

## 6. GitNexus のライセンス（PolyForm Noncommercial 1.0.0）

**確認したもの**: npm の `gitnexus@1.6.12`（実験で使う版）の `package.json` は `"license": "PolyForm-Noncommercial-1.0.0"`。パッケージ内に LICENSE ファイルは無い。リポジトリ（abhigyanpatwari/GitNexus）の `LICENSE` は PolyForm Noncommercial License 1.0.0 の全文（2026-02-03 に追加、`docs: update license copyright holder`）。依存 260 件あまりの内訳は MIT 185・ISC 16・BSD-3-Clause 14・Apache-2.0 10・BlueOak-1.0.0 9・LGPL-3.0-or-later 2 など（PolyForm は GitNexus 本体の 1 件）。

**条文の要点**（事実）:

- 「Noncommercial Purposes: Any noncommercial purpose is a permitted purpose.」
- 「Personal Uses: Personal use for research, experiment, and testing for the benefit of public knowledge, personal study, … without any anticipated commercial application, is use for a permitted purpose.」
- 「Noncommercial Organizations: 慈善団体・教育機関・公的研究機関・政府機関による利用は、資金源にかかわらず permitted purpose。」
- 配布するときは、条文（または URL）と `Required Notice: Copyright Abhigyan Patwari (https://github.com/abhigyanpatwari/GitNexus)` を添えること（Notices）。
- 改変・派生も permitted purpose の範囲で許される。特許の防御条項と、違反時に 32 日以内に是正すれば継続できる規定がある。

**判断**（解釈）: 商用の応用を予定しない、公開を目的とした研究・論文での利用は、条文の permitted purpose に入ると読める。実験で使う条件は次のとおり。

1. 実施の立場: 個人の研究、または非営利・教育・公的研究機関としての実施であること。営利企業の業務として行う場合は「noncommercial purpose」から外れる恐れがある（この実験は個人の研究として行う前提。**利用者の確認が要る**）。
2. 配布しない: 道具を含むイメージは公開せず、`docker/tools/gitnexus/Dockerfile`（npm から取得する手順）と `package-lock.json` だけを公開する。もしイメージを配るなら、条文と上の Required Notice を添える義務が生じる。
3. 結果の公開: 測定値や他の道具との比較の公表を制限する条項は無い（ベンチマーク公開の禁止条項は無い）。
4. 論文には、GitNexus が PolyForm Noncommercial 1.0.0 であることと、非営利の研究として使ったことを書く。

**実験上の扱い**: グラフ系の候補として残す。ただし c3（Linux）は 16GiB のメモリ制限で OOM のため対象外（下の表）。

## 7. L1（検索単体）に向けたメモ

L1 は「質問 → 順位付きの結果」を、エージェントなしで決定的に取る層。L0 で分かった、各道具の呼び方・順位・常駐の要否をまとめる。検索の時間は L0 の `query`（コンテナの起動と道具の立ち上げを含む 1 回の値）。

| 道具 | L1 での呼び方 | 順位 | 常駐 | 備考 |
|---|---|---|---|---|
| ctags | `readtags -t <tags> -n -e -` | なし（定義の一覧。規則で定義→参照の順に並べる） | 不要（即時） | 識別子が既知である前提 |
| GNU global | `global -x` / `global -rx` | なし（定義→参照、パス順） | 不要 | 同上 |
| cscope | `cscope -d -L -1/-3` | なし | 不要 | C のみ（c3） |
| ast-grep | `ast-grep run --pattern` | なし（一致の順） | 不要 | 索引を持たないので毎回全走査。c3 で 1 回 16.6 秒 |
| probe | `probe search` | あり（BM25） | 不要 | 同じく索引なし。c3 で 1 回 55.7 秒 |
| zoekt | `zoekt -index_dir <shards> "sym:X"` など | あり（道具の既定） | 不要（索引を読むだけ） | c3 でも 1 回 1 秒未満。質問の変換（`sym:` を付けるか）を事前登録で決める |
| wikictl | `wikictl grep` | なし（パス順） | 不要 | c1・c4 の文書だけ |
| semble | `semble search -k 10` | あり（埋め込みの類似度） | 望ましい | CLI の起動ごとにモデルを読む。c2 で 1 回 10.8 秒 |
| ck | `ck --hybrid --topk 10 --scores` | あり（正規表現と意味検索の RRF） | 望ましい | **索引が作れるのは c1 のみ**（c2・c3・c4 は 60 分で未完了）。L1 の対象は c1 に限る |
| qmd | `qmd query --no-rerank -n 10` | あり（BM25＋ベクトルの RRF） | 望ましい | Markdown のみ（c1・c2・c4）。GPU。問い合わせの展開に LLM を使い、`llm_cache` に残るので 2 回目以降が速く見える点に注意（同じ質問を測り直すときはキャッシュを消す） |
| codebase-memory-mcp | `codebase-memory-mcp cli search_graph --project … --limit 10` | あり（BM25 のスコア付き） | 望ましい（`daemon start`。CLI は毎回一時デーモンを起動する旨の hint を出す） | 本来は MCP。c3 は既定のメモリ予算 4GiB では失敗し、`CBM_MEM_BUDGET_MB=12288` で成功 |
| codegraph | `codegraph query <symbol> -p <repo> --limit 10 --json` | あり（順位付きの配列。順序の規則は道具の内部。**要確認**） | 不要（SQLite を読む） | 識別子での検索に強い。文書（Markdown）は索引に入らない |
| GitNexus | `gitnexus query <symbol> -r <name>` | 分類ごとの配列（definitions・processes など）。純粋な順位ではない | 不要 | c3 は OOM。`--index-only` を付けないと AGENTS.md・CLAUDE.md・`.claude/skills/` をリポジトリに書き込む |
| graphify | `graphify query <symbol> --graph … --budget N` | 順位ではなく BFS の近傍をトークン予算で切った列挙 | 不要 | LLM なしの tree-sitter の抽出だけで使う |
| Serena | `find_symbol` → `find_referencing_symbols`（MCP のツールを直接呼ぶ） | なし（定義・参照の集合） | **必要** | 問い合わせのたびに言語サーバを起動し直すため、c2 で 1 回 23.8 秒。MCP で常駐させる前提。S1・S3 では正解と同じ解析器になりうるので比較から外す（計画どおり） |

L1 の設計に効く点:

- 順位のない道具（ctags・global・cscope・ast-grep・wikictl・Serena）は、事前登録した規則で順位に直す必要がある（計画の「L1 での順位の規則」）。graphify と GitNexus も、出力を順位に直す規則を決める必要がある（**未決**）。
- 常駐が要る道具（Serena、semble、ck、qmd、codebase-memory-mcp）は、L1 の測定でプロセスを立ち上げたままにするか、1 回ごとの起動を含めて測るかで時間が大きく変わる。L1 の「1 回の問い合わせの時間」は**常駐した状態で測り、立ち上げの時間は別に報告する**ことを提案する（**要決定**）。
- 題材と道具の当てはまりは L0 のとおりで、不完全な設計（qmd は Markdown、cscope は C、wikictl は文書、ck は c1 だけ）を分析計画の対比の一覧に反映する必要がある。
- qmd の検索は言語をまたぐ。c4 の英語の問い合わせ（`pod security admission`）で、上位に中国語訳（`content/zh-cn/...`）のページが並んだ。多言語の題材では「同じ内容の別の言語のページ」を正解に含めるか除くかを、事前登録で決める必要がある（**要決定**。RQ3 の言語の比較に直接効く）。

## 8. 再現の手順

```sh
export ARE_DATA=<題材と索引の置き場所>
scripts/fetch_corpora.py c1 c2 c3 c4
scripts/l0.py build ctags global cscope ast-grep probe zoekt wikictl semble qmd ck \
  codebase-memory-mcp codegraph gitnexus graphify serena
scripts/l0.py batch --slot a --tools ctags,global,cscope,ast-grep,probe,zoekt,wikictl,semble,codebase-memory-mcp,codegraph,gitnexus
scripts/l0.py batch --slot c --tools qmd
scripts/l0.py batch --slot b --tools ck,graphify
scripts/l0.py batch --slot a --tools serena
scripts/l0.py run codebase-memory-mcp c3 --slot a --variant mem12g --env CBM_MEM_BUDGET_MB=12288
scripts/l0.py run serena c2 --slot b --variant go+ts --env "SERENA_C2_LANGS=go typescript"
scripts/l0.py run semble c3 --slot a --variant mem32g --memory 32g
scripts/l0_table.py > /tmp/table.md
```

道具の版はイメージのラベル `l0.version`（記録の `version`）にある。
