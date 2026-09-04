"""Background consolidation (plan §6): defer memory writes off the calling thread.

**Honesty note on how "background" this actually is** (the task's build report
explicitly asked for this): LangMem ships a real background primitive,
`langmem.ReflectionExecutor` -- it was evaluated directly against the installed
0.0.30 source (site-packages/langmem/reflection.py), not just its docs. It's a
genuine option but a poor fit for this module specifically:

1. `LocalReflectionExecutor.__init__` requires `reflector.namespace` to already
   exist -- it does `getattr(reflector, "namespace", None)` and raises if
   missing. That attribute is only ever set by LangMem's own LLM-driven
   memory-manager factories (`create_memory_store_manager` et al.), which
   means the "reflector" has to be an LLM extraction chain, not an arbitrary
   deferred callable -- and every remember_*() call would then cost a Sarvam
   API call (against `app.llm.get_chat_model()`'s Redis-guarded daily budget)
   just to persist a value. remember_preference() shouldn't cost an LLM call
   at all: it's a plain key overwrite.
2. `.submit()` calls `get_config()` to read a LangGraph `RunnableConfig` (or
   requires one passed explicitly) and, when no `store=` was given at
   construction, resolves the store from `config[CONF][CONFIG_KEY_RUNTIME]` --
   i.e. it assumes it's being called from inside a compiled graph's node
   execution. This module needs to work from a future FastAPI chat endpoint
   too, not only from inside a graph node, so leaning on that runtime
   resolution would be the wrong coupling even where it works.

So: a plain `concurrent.futures.ThreadPoolExecutor` deferred-write, which is
what the task explicitly sanctions as a fallback. It is genuinely
non-blocking (submit() returns immediately; the write happens on a worker
thread) and genuinely off the hot path -- it is not a synchronous call
dressed up as async. What it is *not* is dedup/extraction intelligence: the
consolidation logic itself (see procedural.py's pattern-fingerprinting) is
plain Python run on that worker thread, not an LLM call. Swapping in
`ReflectionExecutor` + `create_memory_store_manager` later, once a live LLM
budget is comfortable spending calls on it, only touches this module -- the
remember_*()/recall_*() call sites in semantic.py/episodic.py/procedural.py
would not need to change.
"""

from __future__ import annotations

import atexit
import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Any, Callable

_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="memory-consolidation")
_pending: set[Future] = set()
_pending_lock = threading.Lock()

atexit.register(_EXECUTOR.shutdown, wait=False)


def defer(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
    """Run fn(*args, **kwargs) on a background worker thread; returns immediately."""
    future = _EXECUTOR.submit(fn, *args, **kwargs)
    with _pending_lock:
        _pending.add(future)
    future.add_done_callback(_forget)
    return future


def _forget(future: Future) -> None:
    with _pending_lock:
        _pending.discard(future)


def wait_for_pending(timeout: float | None = None) -> None:
    """Block until every deferred write submitted so far has completed.

    Test/shutdown helper only -- calling this from a live request path would
    defeat the entire point of deferring the write in the first place.
    """
    with _pending_lock:
        futures = list(_pending)
    wait(futures, timeout=timeout)
