"""Model clients. One interface, `complete(system, user) -> str`.

OpenAICompatLLM speaks the OpenAI chat completions format, so it works with OpenAI, Groq, OpenRouter, a local
llama.cpp or Ollama server, and anything else that copies the format. The key comes from the environment and is never
logged or put in an error message. KeywordStandIn is NOT a model: a keyword guesser with the same interface, used by
CI and by the offline demo, and every result that used it says so.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


class LLMError(RuntimeError):
    pass


class OpenAICompatLLM:
    name = "openai-compatible"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 60.0, retries: int = 6, json_mode: bool = True):
        if not api_key:
            raise LLMError("no API key set")
        self.base_url, self.model, self.timeout, self.retries, self.json_mode = base_url.rstrip("/"), model, timeout, retries, json_mode
        self._key = api_key

    @classmethod
    def from_env(cls, json_mode: bool = True, env=None) -> "OpenAICompatLLM":
        """LLM_BASE_URL, LLM_MODEL and LLM_API_KEY, or GROQ_API_KEY alone for Groq's endpoint."""
        env = os.environ if env is None else env
        key = env.get("LLM_API_KEY") or env.get("GROQ_API_KEY") or ""
        base = env.get("LLM_BASE_URL") or "https://api.groq.com/openai/v1"
        model = env.get("LLM_MODEL") or "openai/gpt-oss-120b"
        return cls(base, key, model, json_mode=json_mode)

    def complete(self, system: str, user: str, *, max_tokens: int = 1500) -> str:
        body = {"model": self.model, "temperature": 0, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if self.model.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = "low"  # a reasoning model can spend the whole budget thinking and return nothing
        elif self.model.startswith("qwen/qwen3"):
            body["reasoning_effort"] = "none"  # answer directly, no thinking text in the reply
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        data = json.dumps(body).encode("utf-8")
        delay = 2.0
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(f"{self.base_url}/chat/completions", data=data, method="POST", headers={
                "Content-Type": "application/json", "Authorization": f"Bearer {self._key}", "User-Agent": "stepchaser/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                text = (payload["choices"][0]["message"].get("content") or "").strip()
                if not text:
                    raise LLMError("the model returned an empty answer")
                return text
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503) and attempt < self.retries:
                    wait = float(exc.headers.get("retry-after") or delay)
                    time.sleep(min(wait, 30.0))
                    delay = min(delay * 2, 30.0)
                    continue
                raise LLMError(f"model endpoint answered HTTP {exc.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError, KeyError, IndexError, json.JSONDecodeError) as exc:
                if attempt < self.retries and not isinstance(exc, (KeyError, IndexError)):
                    time.sleep(delay)
                    delay = min(delay * 2, 30.0)
                    continue
                raise LLMError(f"model call failed: {type(exc).__name__}") from None
        raise LLMError("model call failed after retries")


class ScriptedLLM:
    """Replays a function or a list of answers. Records every prompt so a test can read what the model was shown."""
    name = "scripted"

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def complete(self, system: str, user: str, *, max_tokens: int = 500) -> str:
        self.calls.append((system, user))
        if callable(self.answers):
            out = self.answers(system, user)
        else:
            out = self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]
        if isinstance(out, Exception):
            raise out
        return out if isinstance(out, str) else json.dumps(out)
