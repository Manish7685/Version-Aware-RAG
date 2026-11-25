"""A minimal Gemini API client (RAG-M4 S3): one ``generateContent`` call, standard library only.

The API key is sent only in the ``x-goog-api-key`` header and never appears in an exception,
log or return value. Errors are mapped so that the drafting run can react safely:

- ``RateLimited``: HTTP 429 with a non-zero quota; carries the server's retry delay if given;
- ``TransientError``: HTTP 5xx or a network failure; retried with backoff;
- ``FatalError``: authentication, permission, billing, an unknown model, a quota limit of zero
  (the free tier is unavailable for the model), an exhausted per-day quota or any other client
  error. The run stops; no other model is ever tried;
- ``MalformedResponse``: a 200 response without exactly one complete text candidate.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiError(RuntimeError):
    """Base class; messages never contain the API key or request text."""


class RateLimited(GeminiError):
    def __init__(self, message: str, retry_after: float | None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TransientError(GeminiError):
    pass


class FatalError(GeminiError):
    pass


class MalformedResponse(GeminiError):
    pass


@dataclass(frozen=True)
class GenerationConfig:
    model: str
    temperature: float
    max_output_tokens: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
            "response_mime_type": "application/json",
        }


Post = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]


def _urllib_post(
    url: str, headers: dict[str, str], body: bytes, timeout: float
) -> tuple[int, bytes]:
    request = Request(url, data=body, headers=headers, method="POST")  # noqa: S310 (fixed https base)
    try:
        with urlopen(request, timeout=timeout) as resp:  # noqa: S310
            return int(resp.status), bytes(resp.read())
    except HTTPError as exc:
        try:
            return int(exc.code), bytes(exc.read())
        except OSError:
            return int(exc.code), b""
    except (URLError, OSError, TimeoutError) as exc:
        raise TransientError(f"network error ({type(exc).__name__})") from None


def _retry_delay(error: dict[str, Any]) -> float | None:
    for detail in error.get("details", []) or []:
        delay = detail.get("retryDelay") if isinstance(detail, dict) else None
        if isinstance(delay, str) and (m := re.fullmatch(r"(\d+(?:\.\d+)?)s", delay)):
            return float(m.group(1))
    return None


def _zero_quota(error: dict[str, Any]) -> bool:
    """True when the error says the quota limit itself is 0: no free tier for this model."""
    text = json.dumps(error)
    return bool(re.search(r"limit[\"']?\s*[:=]\s*0\b", text)) or "limit: 0" in str(
        error.get("message", "")
    )


def _daily_quota(error: dict[str, Any]) -> bool:
    """True when a 429 names a per-day quota (retrying today cannot succeed)."""
    return "PerDay" in json.dumps(error)


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        config: GenerationConfig,
        post: Post = _urllib_post,
        timeout: float = 120.0,
    ) -> None:
        if not api_key:
            raise FatalError("no Gemini API key (set VRAG_GEMINI_API_KEY)")
        if not config.model:
            raise FatalError("no model configured (configs/drafting.toml [model] id)")
        self._key, self.config, self._post, self._timeout = api_key, config, post, timeout
        self.last_prompt_tokens: int | None = None  # usageMetadata.promptTokenCount, if reported

    def generate(self, prompt: str) -> str:
        """The text of the single complete candidate for ``prompt``."""
        url = f"{API_BASE}/models/{self.config.model}:generateContent"
        body = json.dumps(
            {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": self.config.temperature,
                    "maxOutputTokens": self.config.max_output_tokens,
                    "responseMimeType": "application/json",
                },
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json", "x-goog-api-key": self._key}
        self.last_prompt_tokens = None
        status, raw = self._post(url, headers, body, self._timeout)
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, ValueError):
            data = {}
        error = data.get("error", {}) if isinstance(data, dict) else {}
        if status == 429:
            if _zero_quota(error):
                raise FatalError("quota limit is 0 for this model: the free tier is not available")
            if _daily_quota(error):
                raise FatalError("daily quota exhausted (HTTP 429); resume after the quota resets")
            raise RateLimited("rate limited (HTTP 429)", _retry_delay(error))
        if status >= 500:
            raise TransientError(f"server error (HTTP {status})")
        if status in (401, 403):
            raise FatalError(f"not authorised (HTTP {status}): key, permission or billing")
        if status == 404:
            raise FatalError("model not found (HTTP 404)")
        if status != 200:
            raise FatalError(f"request rejected (HTTP {status})")
        usage = data.get("usageMetadata") if isinstance(data, dict) else None
        tokens = usage.get("promptTokenCount") if isinstance(usage, dict) else None
        self.last_prompt_tokens = tokens if isinstance(tokens, int) else None
        return _single_text(data)


def _single_text(data: Any) -> str:
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise MalformedResponse("expected exactly one candidate")
    candidate = candidates[0]
    if candidate.get("finishReason") != "STOP":
        raise MalformedResponse(
            f"incomplete candidate (finishReason {candidate.get('finishReason')})"
        )
    parts = (candidate.get("content") or {}).get("parts") or []
    texts = [p.get("text") for p in parts if isinstance(p, dict)]
    strings = [t for t in texts if isinstance(t, str)]
    if not texts or len(strings) != len(texts):
        raise MalformedResponse("candidate has no text")
    return "".join(strings)
