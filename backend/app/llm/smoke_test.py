"""Standalone verification script for backend/app/llm/provider.py (plan §5).

Run directly (from the backend/ directory, or anywhere -- it self-locates):
    python backend/app/llm/smoke_test.py

Loads backend/.env if present (python-dotenv) so SARVAM_API_KEY/LLM_* are
picked up the same way the real service picks them up; otherwise falls back
to whatever is already in the process environment.

What this proves, and how:
  1. get_chat_model() builds a correctly-configured Sarvam-backed chat model
     WITHOUT making a live API call -- inspects base_url/model/auth header.
  2. get_chat_model("openrouter") raises ProviderNotConfiguredError when
     OPENROUTER_API_KEY is blank -- confirms no silent fallback to Sarvam.
  3. The Redis circuit breaker actually PREVENTS the underlying HTTP call
     once the daily counter is at/over the limit -- not just a log line:
       a. counter pre-set to the limit, base_url pointed at an unroutable
          address -> invoke() must raise LLMBudgetExhaustedError. If the
          guard were bypassed, invoke() would instead attempt the (broken)
          network call and raise a connection error, never
          LLMBudgetExhaustedError -- so the exception *type* is the proof.
       b. same broken-base_url model, counter reset below the limit ->
          invoke() must now raise a connection-style error (NOT
          LLMBudgetExhaustedError), proving the harness can actually detect
          a guard that fails to block (i.e. step 3a wasn't a fluke/always-
          raises bug) and that under-budget calls really do reach the
          network layer.
  Spins up a throwaway `redis:7-alpine` container via docker if no Redis is
  reachable at localhost:6379, and stops it again on exit.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        print("[smoke] python-dotenv not installed -- relying on process env only")
        return
    env_path = _BACKEND_DIR / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print(f"[smoke] loaded {env_path}")
    else:
        print(f"[smoke] no {env_path} found -- relying on process env only")


_load_env()
sys.path.insert(0, str(_BACKEND_DIR))

import redis as redis_lib  # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402

from app.llm import (  # noqa: E402
    SARVAM_BASE_URL,
    LLMBudgetExhaustedError,
    ProviderNotConfiguredError,
    get_chat_model,
)
from app.llm.provider import _BudgetGuardedChatOpenAI, _daily_call_key  # noqa: E402


def _redis_reachable(url: str) -> bool:
    try:
        redis_lib.Redis.from_url(url, socket_connect_timeout=1).ping()
        return True
    except Exception:
        return False


def _ensure_local_redis() -> tuple[str, str | None]:
    url = "redis://localhost:6379/0"
    if _redis_reachable(url):
        print("[smoke] using already-reachable Redis at localhost:6379")
        return url, None

    print("[smoke] no Redis reachable at localhost:6379 -- starting redis:7-alpine via docker")
    container_id = (
        subprocess.check_output(["docker", "run", "--rm", "-d", "-p", "6379:6379", "redis:7-alpine"])
        .decode()
        .strip()
    )
    for _ in range(30):
        if _redis_reachable(url):
            break
        time.sleep(0.5)
    else:
        subprocess.run(["docker", "stop", container_id], check=False)
        raise RuntimeError("started redis container but it never became reachable")
    print(f"[smoke] started throwaway redis container {container_id[:12]}")
    return url, container_id


def test_construct_sarvam_model_without_calling_it(redis_url: str) -> None:
    model = get_chat_model("sarvam", redis_url=redis_url)
    assert model.openai_api_base == SARVAM_BASE_URL, model.openai_api_base
    assert model.model_name == os.environ["LLM_MODEL"], model.model_name
    headers = model.default_headers or {}
    assert "api-subscription-key" in headers, "sarvam auth header missing"
    assert headers["api-subscription-key"], "sarvam auth header is empty"
    print(
        "[smoke] PASS: sarvam model constructs with base_url="
        f"{model.openai_api_base}, model={model.model_name}, "
        "api-subscription-key header present (value not printed) -- no network call made"
    )


def test_openrouter_dormant_without_key() -> None:
    saved = os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        get_chat_model("openrouter")
    except ProviderNotConfiguredError as exc:
        print(f"[smoke] PASS: get_chat_model('openrouter') raised ProviderNotConfiguredError: {exc}")
    else:
        raise AssertionError("expected ProviderNotConfiguredError when OPENROUTER_API_KEY is blank")
    finally:
        if saved is not None:
            os.environ["OPENROUTER_API_KEY"] = saved


def test_circuit_breaker_actually_blocks(redis_url: str) -> None:
    provider_name = f"smoketest-{uuid.uuid4().hex[:8]}"
    limit = 1
    unroutable_base_url = "http://127.0.0.1:9/v1"  # port 9 (discard) refuses connections fast

    redis_client = redis_lib.Redis.from_url(redis_url, decode_responses=True)
    key = _daily_call_key(provider_name)
    redis_client.delete(key)

    def build_model() -> _BudgetGuardedChatOpenAI:
        return _BudgetGuardedChatOpenAI(
            model="sarvam-105b",
            api_key="dummy-not-a-real-key",
            base_url=unroutable_base_url,
            default_headers={},
            budget_provider=provider_name,
            budget_daily_limit=limit,
            budget_redis_url=redis_url,
        )

    # 3a: pre-set the counter AT the limit -> the very next call must be blocked
    # before it ever reaches the (deliberately broken) network layer.
    redis_client.set(key, limit)
    model = build_model()
    try:
        model.invoke([HumanMessage(content="hi")])
    except LLMBudgetExhaustedError as exc:
        print(f"[smoke] PASS: over-budget invoke() blocked with LLMBudgetExhaustedError: {exc}")
    except Exception as exc:  # pragma: no cover - this is exactly the failure mode we're checking for
        raise AssertionError(
            "circuit breaker did NOT block the call -- got "
            f"{type(exc).__name__} ({exc}) instead of LLMBudgetExhaustedError, "
            "meaning the guard let the request reach the network layer"
        ) from exc
    else:
        raise AssertionError("expected LLMBudgetExhaustedError, invoke() returned normally")

    # 3b: control -- reset the counter below the limit on the SAME broken-URL
    # model and confirm the call now actually attempts the network request
    # (fails with a connection error, not LLMBudgetExhaustedError). This
    # proves 3a's exception really came from the budget guard, not from some
    # other short-circuit that always fires regardless of the counter.
    redis_client.delete(key)
    model2 = build_model()
    try:
        model2.invoke([HumanMessage(content="hi")])
    except LLMBudgetExhaustedError as exc:  # pragma: no cover
        raise AssertionError(f"guard fired even though the counter was reset below the limit: {exc}") from exc
    except Exception as exc:
        print(
            f"[smoke] PASS: under-budget invoke() reached the network layer and failed with "
            f"{type(exc).__name__} (expected, since base_url is unroutable) -- confirms the guard "
            "in 3a was the actual blocker, not a permanent short-circuit"
        )
    else:  # pragma: no cover
        raise AssertionError("expected a connection error against an unroutable base_url, got none")


def main() -> None:
    redis_url, container_id = _ensure_local_redis()
    try:
        test_construct_sarvam_model_without_calling_it(redis_url)
        test_openrouter_dormant_without_key()
        test_circuit_breaker_actually_blocks(redis_url)
        print("[smoke] ALL CHECKS PASSED")
    finally:
        if container_id is not None:
            subprocess.run(["docker", "stop", container_id], check=False)
            print(f"[smoke] stopped throwaway redis container {container_id[:12]}")


if __name__ == "__main__":
    main()
