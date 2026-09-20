"""Calls to the local Ollama server, one at a time, with a cache.

The cache (gold-work/llm-cache/<sha256>.json) keys on the model, the
messages and the options, so a rerun of a generator gives the same text and
the record of what each model was asked stays next to the answer. Thinking is
turned off and the temperature is 0 with a fixed seed.
"""
import hashlib
import json
import os
import time
import urllib.request

from common import WORK

URL = os.environ.get("ARE_LOCAL_URL", "http://192.168.1.240:8080")
CACHE = os.path.join(WORK, "llm-cache")
GEN_MODEL = "qwen3.8:27b"     # paraphrases, questions from passages, back-translation check
CHECK_MODEL = "gemma4:31b"    # uniqueness, translation, answer judging
OPTIONS = {"temperature": 0, "seed": 1, "num_ctx": 16384}

stats = {"calls": 0, "cached": 0, "seconds": 0.0}


def chat(model, prompt, system=None, fmt=None, options=None):
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    opts = dict(OPTIONS, **(options or {}))
    body = {"model": model, "messages": msgs, "stream": False, "think": False, "options": opts}
    if fmt:
        body["format"] = fmt
    key = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    path = os.path.join(CACHE, key[:2], key + ".json")
    if os.path.exists(path):
        stats["cached"] += 1
        return json.load(open(path))["content"]
    t = time.time()
    for attempt in range(3):
        try:
            req = urllib.request.Request(URL + "/api/chat", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=600) as r:
                j = json.load(r)
            break
        except Exception:  # noqa: BLE001
            if attempt == 2:
                raise
            time.sleep(10)
    content = j["message"]["content"]
    dt = time.time() - t
    stats["calls"] += 1
    stats["seconds"] += dt
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump({"request": body, "content": content, "seconds": dt,
               "eval_count": j.get("eval_count")}, open(path, "w"), ensure_ascii=False)
    return content


def chat_json(model, prompt, system=None, schema=None):
    """Ask for JSON (Ollama's structured output) and parse it; None if the
    model's text is not JSON."""
    txt = chat(model, prompt, system=system, fmt=schema or "json")
    try:
        return json.loads(txt)
    except ValueError:
        return None
