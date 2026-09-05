"""LLM provider abstraction (plan §5).

get_chat_model() returns a LangChain-compatible chat model backed by either
Sarvam or OpenRouter -- both expose OpenAI-style `/v1/chat/completions`
endpoints, so one `ChatOpenAI` subclass covers both via base_url/api_key/model,
no per-provider SDK branching.

Every call the returned model makes (invoke/ainvoke/stream/astream, and
anything built on top via .bind_tools()/.with_structured_output(), since those
wrap the same instance rather than replacing it) is gated by a Redis daily
call-count circuit breaker: the counter is incremented and checked *before*
the underlying HTTP call fires, so once the ceiling is hit the request never
leaves the process -- it fails fast with LLMBudgetExhaustedError instead of
spending Sarvam/OpenRouter credit.

Usage:
    from app.llm import get_chat_model

    model = get_chat_model()                 # provider = env LLM_PROVIDER
    model = get_chat_model("openrouter")      # explicit override
    model.invoke([...])                       # sync -- raises LLMBudgetExhaustedError over budget
    await model.ainvoke([...])                # async -- same guard, same counter
"""

from __future__ import annotations

import functools
import os
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterator

import redis
import redis.asyncio as redis_asyncio
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI
from pydantic import Field

# Confirmed live against Sarvam's own MCP tooling (sarvam_code_snippet/
# sarvam_code_api_reference for /v1/chat/completions), not just assumed from
# docs.sarvam.ai -- see build report.
SARVAM_BASE_URL = "https://api.sarvam.ai/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_PROVIDER_BASE_URLS = {
    "sarvam": SARVAM_BASE_URL,
    "openrouter": OPENROUTER_BASE_URL,
}

# Sarvam authenticates via a custom header, not an OpenAI-style Bearer token
# (confirmed live via the Sarvam MCP server's verified code snippet) -- so the
# api_key handed to ChatOpenAI is a harmless placeholder for Sarvam and the
# real credential travels as `api-subscription-key` in default_headers.
_SARVAM_AUTH_HEADER = "api-subscription-key"

_DAILY_KEY_TTL_SECONDS = 26 * 60 * 60  # outlives a day so a slow clock never drops the key early


class ProviderNotConfiguredError(RuntimeError):
    """Raised when a provider is requested but missing required config (e.g. no API key).

    Deliberately not a silent fallback to another provider -- the plan is
    explicit that OpenRouter is "wired but dormant" until a key is added.
    """


class LLMBudgetExhaustedError(RuntimeError):
    """Raised when the day's Redis-tracked call budget for a provider is used up.

    Callers (FastAPI routes, LangGraph nodes) can map this to HTTP 429.
    """


def _daily_call_key(provider: str) -> str:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"llm_calls:{provider}:{day}"


@functools.lru_cache(maxsize=8)
def _sync_redis_client(redis_url: str) -> redis.Redis:
    return redis.Redis.from_url(redis_url, decode_responses=True)


@functools.lru_cache(maxsize=8)
def _async_redis_client(redis_url: str) -> redis_asyncio.Redis:
    return redis_asyncio.Redis.from_url(redis_url, decode_responses=True)


def _enforce_budget_sync(provider: str, daily_limit: int, redis_url: str) -> None:
    client = _sync_redis_client(redis_url)
    key = _daily_call_key(provider)
    count = client.incr(key)
    if count == 1:
        client.expire(key, _DAILY_KEY_TTL_SECONDS)
    if count > daily_limit:
        raise LLMBudgetExhaustedError(
            f"daily LLM call budget exhausted for provider '{provider}' "
            f"({daily_limit} calls/day, key={key}) -- refusing to call the API"
        )


async def _enforce_budget_async(provider: str, daily_limit: int, redis_url: str) -> None:
    client = _async_redis_client(redis_url)
    key = _daily_call_key(provider)
    count = await client.incr(key)
    if count == 1:
        await client.expire(key, _DAILY_KEY_TTL_SECONDS)
    if count > daily_limit:
        raise LLMBudgetExhaustedError(
            f"daily LLM call budget exhausted for provider '{provider}' "
            f"({daily_limit} calls/day, key={key}) -- refusing to call the API"
        )


def get_daily_call_count(provider: str, redis_url: str) -> int:
    """Read-only lookup of today's call count for `GET /api/settings/usage`
    (plan SP-B §4) -- a plain Redis GET, never an INCR, so checking usage
    stats never itself counts against the budget it's reporting on."""
    client = _sync_redis_client(redis_url)
    value = client.get(_daily_call_key(provider))
    return int(value) if value is not None else 0


class _BudgetGuardedChatOpenAI(ChatOpenAI):
    """ChatOpenAI subclass, not a wrapping Runnable.

    A composition wrapper would lose the guard the moment a caller does
    `model.bind_tools([...])` or `.with_structured_output(...)`, since those
    return a RunnableBinding around whatever instance they're called on --
    if that instance were the *raw* ChatOpenAI, tool-calling paths (exactly
    how the SQL agent cluster will use this) would bypass the budget check
    entirely. Subclassing keeps `bound=self` pointing at the guarded instance
    in every case, because `_generate`/`_agenerate`/`_stream`/`_astream` are
    the hook methods invoke/ainvoke/stream/astream and bind_tools()-wrapped
    calls all bottom out in.
    """

    budget_provider: str = Field(...)
    budget_daily_limit: int = Field(...)
    budget_redis_url: str = Field(...)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        _enforce_budget_sync(self.budget_provider, self.budget_daily_limit, self.budget_redis_url)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        await _enforce_budget_async(self.budget_provider, self.budget_daily_limit, self.budget_redis_url)
        return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        _enforce_budget_sync(self.budget_provider, self.budget_daily_limit, self.budget_redis_url)
        yield from super()._stream(*args, **kwargs)

    async def _astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        await _enforce_budget_async(self.budget_provider, self.budget_daily_limit, self.budget_redis_url)
        async for chunk in super()._astream(*args, **kwargs):
            yield chunk


def _resolve_credentials(provider: str) -> tuple[str, dict[str, str]]:
    if provider == "sarvam":
        api_key = os.environ.get("SARVAM_API_KEY", "")
        if not api_key:
            raise ProviderNotConfiguredError("SARVAM_API_KEY is not set")
        return api_key, {_SARVAM_AUTH_HEADER: api_key}

    if provider == "openrouter":
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise ProviderNotConfiguredError(
                "provider='openrouter' was requested but OPENROUTER_API_KEY is empty/unset -- "
                "OpenRouter is wired but dormant until a key is configured"
            )
        return api_key, {}

    raise ProviderNotConfiguredError(
        f"unknown LLM provider '{provider}' -- expected one of {sorted(_PROVIDER_BASE_URLS)}"
    )


def get_chat_model(
    provider: str | None = None,
    *,
    model: str | None = None,
    daily_call_limit: int | None = None,
    redis_url: str | None = None,
    **model_kwargs: Any,
) -> _BudgetGuardedChatOpenAI:
    """Build a budget-guarded, LangChain-compatible chat model.

    Works from both sync and async LangGraph node contexts -- call
    `.invoke(...)` or `await .ainvoke(...)` on the same returned instance;
    both paths route through the same Redis-backed daily counter, so there is
    no separate get_chat_model_async().

    Args:
        provider: "sarvam" | "openrouter". Defaults to env LLM_PROVIDER.
        model: model name. Defaults to env LLM_MODEL.
        daily_call_limit: override for env LLM_DAILY_CALL_LIMIT (default 500).
        redis_url: override for env REDIS_URL.
        **model_kwargs: forwarded to ChatOpenAI (e.g. temperature, max_tokens).

    Raises:
        ProviderNotConfiguredError: unknown provider, or the provider's API
            key env var is empty/unset (OpenRouter is dormant by design until
            OPENROUTER_API_KEY is set -- this is not a silent fallback).
    """
    resolved_provider = (provider or os.environ.get("LLM_PROVIDER") or "").strip().lower()
    if not resolved_provider:
        raise ProviderNotConfiguredError("no provider given and LLM_PROVIDER is not set")
    if resolved_provider not in _PROVIDER_BASE_URLS:
        raise ProviderNotConfiguredError(
            f"unknown LLM provider '{resolved_provider}' -- expected one of {sorted(_PROVIDER_BASE_URLS)}"
        )

    resolved_model = model or os.environ.get("LLM_MODEL")
    if not resolved_model:
        raise ProviderNotConfiguredError("no model given and LLM_MODEL is not set")

    api_key, extra_headers = _resolve_credentials(resolved_provider)

    resolved_limit = (
        daily_call_limit if daily_call_limit is not None else int(os.environ.get("LLM_DAILY_CALL_LIMIT", "500"))
    )
    resolved_redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    return _BudgetGuardedChatOpenAI(
        model=resolved_model,
        api_key=api_key,
        base_url=_PROVIDER_BASE_URLS[resolved_provider],
        default_headers=extra_headers,
        budget_provider=resolved_provider,
        budget_daily_limit=resolved_limit,
        budget_redis_url=resolved_redis_url,
        **model_kwargs,
    )
