次の質問に答えてください。答えは題材の中から探し、題材の外の知識で推測しないでください。

## 環境

{ENV}

## 質問

{QUESTION}

## 回答

`/workspace/answer.json` に、次の形の JSON を書いてください。

```json
{"answer": <回答>, "evidence": ["<根拠にしたファイルのパス>"]}
```

- `answer` の形: {ANSWER_FORMAT}
- 題材に答えが無いと判断したときは、`answer` を `null` にしてください。
- `evidence` には、答えの根拠にしたファイルを、`/corpus` からの相対パス（例: `wikictl/README.md`）で書いてください。
- `answer.json` を書いたら終了してください。
