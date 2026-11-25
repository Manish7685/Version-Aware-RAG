"""The Gemini client's error mapping and key handling, with a fake transport (no network)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from version_aware_rag.benchmark.gemini import (
    FatalError,
    GeminiClient,
    GenerationConfig,
    MalformedResponse,
    RateLimited,
    TransientError,
)
from version_aware_rag.settings import Settings

KEY = "AIza" + "FAKE" + "0" * 28  # dummy key-shaped value for the key-like guard; not a credential
CONFIG = GenerationConfig("test-model", 0.2, 256)


def _client(status: int, body: Any, seen: list[Any] | None = None) -> GeminiClient:
    def post(url: str, headers: dict[str, str], data: bytes, timeout: float) -> tuple[int, bytes]:
        if seen is not None:
            seen.append((url, headers, data))
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        return status, raw

    return GeminiClient(KEY, CONFIG, post=post)


def _ok(text: str = '{"a": 1}', finish: str = "STOP", n: int = 1) -> dict[str, Any]:
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}] * n}


def test_success_sends_the_key_only_in_the_header() -> None:
    seen: list[Any] = []
    assert _client(200, _ok(), seen).generate("prompt") == '{"a": 1}'
    url, headers, data = seen[0]
    assert headers["x-goog-api-key"] == KEY and KEY not in url and KEY.encode() not in data
    body = json.loads(data)
    assert body["generationConfig"] == {
        "temperature": 0.2,
        "maxOutputTokens": 256,
        "responseMimeType": "application/json",
    }
    assert url.endswith("/models/test-model:generateContent")


@pytest.mark.parametrize(
    ("status", "body", "error", "match"),
    [
        (429, {"error": {"details": [{"retryDelay": "17s"}]}}, RateLimited, "429"),
        (
            429,
            {"error": {"message": "Quota exceeded, limit: 0, model: x"}},
            FatalError,
            "free tier",
        ),
        (503, b"", TransientError, "503"),
        (401, {}, FatalError, "not authorised"),
        (403, {}, FatalError, "billing"),
        (404, {}, FatalError, "model not found"),
        (400, {}, FatalError, "rejected"),
        (200, {"candidates": []}, MalformedResponse, "exactly one"),
        (200, _ok(n=2), MalformedResponse, "exactly one"),
        (200, _ok(finish="MAX_TOKENS"), MalformedResponse, "incomplete"),
        (
            200,
            {"candidates": [{"finishReason": "STOP", "content": {"parts": []}}]},
            MalformedResponse,
            "no text",
        ),
        (200, b"not json", MalformedResponse, "exactly one"),
    ],
)
def test_errors_are_mapped_without_the_key(
    status: int, body: Any, error: type[Exception], match: str
) -> None:
    with pytest.raises(error, match=match) as info:
        _client(status, body).generate("prompt")
    assert KEY not in str(info.value)


def test_retry_delay_is_read_from_the_error() -> None:
    with pytest.raises(RateLimited) as info:
        _client(429, {"error": {"details": [{"retryDelay": "17s"}]}}).generate("p")
    assert info.value.retry_after == 17.0


def test_no_key_or_no_model_refuses_to_build_a_client() -> None:
    with pytest.raises(FatalError, match="VRAG_GEMINI_API_KEY"):
        GeminiClient("", CONFIG)
    with pytest.raises(FatalError, match="no model"):
        GeminiClient(KEY, GenerationConfig("", 0.2, 256))


def test_the_key_setting_is_secret_and_never_dumped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VRAG_GEMINI_API_KEY", KEY)
    settings = Settings(_env_file=None)
    assert settings.gemini_api_key is not None and settings.gemini_api_key.get_secret_value() == KEY
    assert KEY not in repr(settings) and KEY not in json.dumps(settings.model_dump(mode="json"))
    assert "gemini_api_key" not in settings.model_dump()


def test_the_prompt_token_count_is_kept_when_reported() -> None:
    body = {**_ok(), "usageMetadata": {"promptTokenCount": 1234}}
    client = _client(200, body)
    client.generate("p")
    assert client.last_prompt_tokens == 1234
    assert _client(200, _ok()).last_prompt_tokens is None


def test_an_exhausted_daily_quota_stops_instead_of_retrying() -> None:
    error = {
        "error": {
            "details": [
                {"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}
            ]
        }
    }
    with pytest.raises(FatalError, match="daily quota exhausted"):
        _client(429, error).generate("p")
