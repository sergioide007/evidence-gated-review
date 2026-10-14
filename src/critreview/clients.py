"""Provider-agnostic model clients.

`FakeClient` is deterministic and used by tests/evals. The live adapters are thin stdlib (urllib) clients:
`AnthropicClient`, and ONE generic `OpenAICompatClient` that serves every provider speaking the OpenAI
chat-completions format (openai, gemini, groq, deepseek). NOTE: the live adapters follow the public API shapes
but have NOT been exercised against live endpoints in this repository's CI (tests use a fake HTTP layer).
Run `critreview doctor` and `critreview eval --live` yourself, and verify current model IDs and prices.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Protocol

from .resilience import TransientError


@dataclass
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


class ModelClient(Protocol):
    def complete(self, system: str, prompt: str, max_output_tokens: int) -> Completion: ...


class FakeClient:
    """Routes by marker found in the prompt, e.g. '[[LENS:security]]'. Fully deterministic."""

    def __init__(self, responses: dict[str, str], default: str = '{"findings": []}', model: str = "fake"):
        self.responses = responses
        self.default = default
        self.model = model
        self.calls: list[str] = []

    def complete(self, system: str, prompt: str, max_output_tokens: int) -> Completion:
        key = next((k for k in self.responses if k in prompt), None)
        text = self.responses[key] if key else self.default
        self.calls.append(key or "<default>")
        return Completion(text, max(1, len(system + prompt) // 4), max(1, len(text) // 4), self.model)


class ProviderError(Exception):
    """Non-retryable provider failure (bad key, unknown model, request too large...). Message is key-safe."""


def scrub(text: str, *secrets: str) -> str:
    for sec in secrets:
        if sec:
            text = text.replace(sec, "[REDACTED]")
    return text


def _retry_after(e: urllib.error.HTTPError) -> float | None:
    try:
        v = e.headers.get("Retry-After") if e.headers else None
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _request_json(method: str, url: str, headers: dict[str, str], body: dict | None, timeout: float,
                  secret: str = "") -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed https URLs)
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = scrub(e.read().decode("utf-8", "replace"), secret)[:300]
        except Exception:
            pass
        if e.code in (408, 409, 429) or e.code >= 500:
            raise TransientError(f"HTTP {e.code} {detail}".strip(), retry_after=_retry_after(e)) from e
        raise ProviderError(f"HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise TransientError(scrub(str(e), secret)) from e


def _post_json(url: str, headers: dict[str, str], body: dict, timeout: float, secret: str = "") -> dict:
    return _request_json("POST", url, headers, body, timeout, secret)


class AnthropicClient:
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str, api_key_env: str = "ANTHROPIC_API_KEY", timeout: float = 120.0):
        self.model, self.api_key_env, self.timeout = model, api_key_env, timeout

    def complete(self, system: str, prompt: str, max_output_tokens: int) -> Completion:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise ProviderError(f"{self.api_key_env} is not set")
        data = _post_json(self.URL, {"x-api-key": key, "anthropic-version": "2023-06-01",
                                     "content-type": "application/json"},
                          {"model": self.model, "max_tokens": max_output_tokens, "system": system,
                           "messages": [{"role": "user", "content": prompt}]}, self.timeout, secret=key)
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        u = data.get("usage", {})
        return Completion(text, u.get("input_tokens", 0), u.get("output_tokens", 0), self.model)


@dataclass(frozen=True)
class Provider:
    base_url: str
    key_envs: tuple[str, ...]      # first one found wins
    max_tokens_param: str          # OpenAI's newer models want max_completion_tokens; the others accept max_tokens
    data_note: str = ""            # shown by `critreview doctor`


# Providers that speak the OpenAI chat-completions format. URLs verified against provider docs in 2026-10;
# model IDs are NOT listed here on purpose: they change too fast. Use `critreview models --provider X`.
PROVIDERS: dict[str, Provider] = {
    "openai": Provider("https://api.openai.com/v1", ("OPENAI_API_KEY",), "max_completion_tokens"),
    "gemini": Provider("https://generativelanguage.googleapis.com/v1beta/openai",
                       ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "max_tokens",
                       "Gemini free tier: Google's terms say prompts may be used to improve its products. "
                       "Do not send private or proprietary code through it."),
    "groq": Provider("https://api.groq.com/openai/v1", ("GROQ_API_KEY",), "max_tokens",
                     "Groq free tier: limits are per organization and strict (tokens per minute); "
                     "extra keys do not add capacity."),
    "deepseek": Provider("https://api.deepseek.com", ("DEEPSEEK_API_KEY",), "max_tokens",
                         "DeepSeek is pay-as-you-go; check your account for credits. Servers are in China: "
                         "consider that before sending proprietary code."),
}
KNOWN_PROVIDERS = tuple(PROVIDERS) + ("anthropic",)


def find_key(provider: str) -> tuple[str | None, str]:
    """Return (key or None, name of the env var that supplied it / the preferred name)."""
    names = PROVIDERS[provider].key_envs
    for n in names:
        if os.environ.get(n):
            return os.environ[n], n
    return None, names[0]


class OpenAICompatClient:
    """One client for every OpenAI-compatible provider (openai, gemini, groq, deepseek)."""

    def __init__(self, provider: str, model: str, timeout: float = 120.0):
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider: {provider}")
        self.provider, self.model, self.timeout = provider, model, timeout

    def complete(self, system: str, prompt: str, max_output_tokens: int) -> Completion:
        spec = PROVIDERS[self.provider]
        key, env = find_key(self.provider)
        if not key:
            raise ProviderError(f"{env} is not set (export it or put it in .env)")
        data = _post_json(spec.base_url + "/chat/completions",
                          {"Authorization": f"Bearer {key}", "content-type": "application/json"},
                          {"model": self.model, spec.max_tokens_param: max_output_tokens,
                           "messages": [{"role": "system", "content": system},
                                        {"role": "user", "content": prompt}]}, self.timeout, secret=key)
        try:
            text = data["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError, AttributeError) as e:
            raise ProviderError(f"unexpected response shape from {self.provider}") from e
        u = data.get("usage") or {}
        tin, tout = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
        if tin == 0:   # some free endpoints omit usage: fall back to an estimate so budgets still bind
            tin = max(1, len(system + prompt) // 4)
        if tout == 0 and text:
            tout = max(1, len(text) // 4)
        return Completion(text, tin, tout, self.model)


def list_models(provider: str, timeout: float = 30.0) -> list[str]:
    """IDs the account can use, straight from the provider (GET /models). Spends no tokens."""
    key, env = find_key(provider)
    if not key:
        raise ProviderError(f"{env} is not set")
    data = _request_json("GET", PROVIDERS[provider].base_url + "/models", {"Authorization": f"Bearer {key}"},
                         None, timeout, secret=key)
    ids = [str(m.get("id", "")) for m in data.get("data", []) if isinstance(m, dict)]
    return sorted({i.removeprefix("models/") for i in ids if i})


def build_client(provider: str, model: str) -> ModelClient:
    if provider == "anthropic":
        return AnthropicClient(model)
    if provider in PROVIDERS:
        return OpenAICompatClient(provider, model)
    raise ValueError(f"unknown provider: {provider} (known: {', '.join(KNOWN_PROVIDERS)})")


class GuardedClient:
    """Wraps a client with budget enforcement (atomic reservation), circuit breaker, bounded retries and audit."""

    def __init__(self, inner: ModelClient, tier: str, budget, breaker, retry: Callable,
                 price_in: float = 0.0, price_out: float = 0.0, on_call: Callable[[dict], None] | None = None,
                 limiter=None):
        self.inner, self.tier, self.budget, self.breaker, self.retry = inner, tier, budget, breaker, retry
        self.price_in, self.price_out, self.on_call, self.limiter = price_in, price_out, on_call, limiter

    def complete(self, system: str, prompt: str, max_output_tokens: int) -> Completion:
        est = self.budget.reserve(max(1, len(system + prompt) // 4))
        try:
            if self.limiter is not None:   # wait for the provider's per-minute window instead of eating 429s
                self.limiter.acquire(est + max_output_tokens)
            comp = self.breaker.call(lambda: self.retry(lambda: self.inner.complete(system, prompt, max_output_tokens)))
        except BaseException:
            self.budget.release(est)
            raise
        if self.on_call:
            from .audit import sha256_text
            self.on_call({"tier": self.tier, "model": comp.model, "system_sha256": sha256_text(system),
                          "prompt_sha256": sha256_text(prompt), "response_sha256": sha256_text(comp.text),
                          "input_tokens": comp.input_tokens, "output_tokens": comp.output_tokens})
        self.budget.charge(self.tier, comp.input_tokens, comp.output_tokens, self.price_in, self.price_out,
                           reserved_in=est)
        return comp
