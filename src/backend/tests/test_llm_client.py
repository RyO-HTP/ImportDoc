from __future__ import annotations

from random import Random
from types import SimpleNamespace

import pymupdf
import pytest

from app.config import LLMSettings
from app.services.llm import DEFAULT_OUTPUT_TOKENS, LLMClient, LLMError, LLMTruncated


def _client(monkeypatch, choices):
    settings = LLMSettings(api_key="k", base_url="http://x/v1", text_model="sonya", vision_model="vision_sonya")

    async def fake_create(**kwargs):
        return SimpleNamespace(choices=choices)

    client = LLMClient(settings)
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    return client


async def test_truncated_reasoning_raises_clear_error(monkeypatch):
    choice = SimpleNamespace(
        message=SimpleNamespace(content="", reasoning_content="suy nghĩ rất dài"),
        finish_reason="length",
    )
    client = _client(monkeypatch, [choice])
    with pytest.raises(LLMTruncated, match="suy luận"):
        await client.complete_json("prompt", "extraction", None)


async def test_json_in_fenced_block_is_parsed(monkeypatch):
    choice = SimpleNamespace(message=SimpleNamespace(content='```json\n{"ok": true}\n```'), finish_reason="stop")
    client = _client(monkeypatch, [choice])
    assert await client.complete_json("prompt", "x", None) == {"ok": True}


async def test_non_json_content_raises(monkeypatch):
    choice = SimpleNamespace(message=SimpleNamespace(content="Tôi không thể trả lời."), finish_reason="stop")
    client = _client(monkeypatch, [choice])
    with pytest.raises(LLMError):
        await client.complete_json("prompt", "x", None)


async def test_large_deepseek_scan_uses_compressed_jpeg_and_streams_json(monkeypatch):
    client = _client(monkeypatch, [])
    client.settings.vision_model = "bioturing/deepseek-v4.1-flash"
    samples = Random(2).randbytes(500 * 500 * 3)
    png = pymupdf.Pixmap(pymupdf.csRGB, 500, 500, samples, False).tobytes("png")
    requests = []

    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def __aiter__(self):
            for content, finish_reason in [('{"ok":', None), ('true}', "stop")]:
                choice = SimpleNamespace(delta=SimpleNamespace(content=content), finish_reason=finish_reason)
                yield SimpleNamespace(choices=[choice])

    async def create(**kwargs):
        requests.append(kwargs)
        return Stream()

    client.client.chat.completions.create = create
    assert await client.complete_json("prompt", "extraction", image_png=png) == {"ok": True}
    request, = requests
    assert request["stream"] is True
    url = request["messages"][1]["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    assert len(url) < len(png)


async def test_default_output_budget_is_generous():
    assert DEFAULT_OUTPUT_TOKENS >= 16000


async def test_truncation_is_not_retried(monkeypatch):
    """Regression: a reasoning model that burns the whole budget must fail once, not 3x."""
    calls = {"n": 0}

    class TruncatingClient:
        def __init__(self):
            self.settings = SimpleNamespace(max_tokens=32000)

        async def complete_json(self, prompt, schema_name, schema=None, image_png=None, max_tokens=None, model_override=None):
            calls["n"] += 1
            raise LLMTruncated("hết budget")

    from app.services.llm import LLMClient as RealClient

    client = RealClient.__new__(RealClient)
    client.settings = SimpleNamespace(max_tokens=32000)
    client.capability = "json_prompt"
    client.client = None
    client.complete_json = TruncatingClient().complete_json  # type: ignore[method-assign]
    with pytest.raises(LLMTruncated):
        await RealClient.complete_json_validated(client, "p", "extraction", None, lambda d: d)
    assert calls["n"] == 1


def test_budget_covers_reasoning_plus_answer():
    from app.services.llm import MIN_OUTPUT_TOKENS, budget_for

    small = budget_for(3, False)
    large = budget_for(14, True)
    assert small >= MIN_OUTPUT_TOKENS
    assert large > small
    assert large >= 10000
