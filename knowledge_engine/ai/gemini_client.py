"""Optional Gemini transport used only by the provisional draft planner.

The runtime reads the API key from the process environment and never writes or
prints it.  Deterministic validation remains the authority over all generated
drafts.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_MODELS = ("gemini-flash-lite-latest", "gemini-2.5-flash")


def _api_key() -> str:
    value = os.environ.get("GEMINI_API_KEY", "").strip()
    if not value:
        raise RuntimeError("GEMINI_API_KEY is not set; use deterministic planning or configure it in the process environment.")
    return value


def _models() -> list[str]:
    configured = os.environ.get("GEMINI_MODEL", "").strip()
    return [configured] if configured else list(DEFAULT_MODELS)


def run_llm_prompt(prompt: str, timeout_seconds: int = 180) -> str:
    """Call Gemini directly without importing legacy orchestration code."""
    api_key = _api_key()
    errors: list[str] = []
    for model in _models():
        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{urllib.parse.quote(model, safe='')}:generateContent?key="
            f"{urllib.parse.quote(api_key, safe='')}"
        )
        request = urllib.request.Request(
            endpoint,
            data=json.dumps({
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0, "candidateCount": 1},
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
            parts = (payload.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
            text = "\n".join(str(part.get("text") or "") for part in parts).strip()
            if text:
                return text
            errors.append(f"{model}: empty response")
        except urllib.error.HTTPError as exc:
            errors.append(f"{model}: HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            errors.append(f"{model}: {type(exc).__name__}")
    raise RuntimeError("Gemini request failed for configured models: " + "; ".join(errors))
