"""Chat history feature: threads (list/create/rename/delete), per-thread
messages, the "select something to chat with" scope picker, and POST /chat
itself.

No login exists in this build (see frontend/src/api/apiClient.ts and
state/AppStateContext.tsx -- neither attaches a cookie or client id), so
there is still no per-browser identity to key anything on. What changed from
this file's original single-implicit-thread-per-persona design is WHERE the
"no identity" latitude gets spent: episodic memory's `user_id` namespace slot
(app/memory/namespaces.py's `(persona_id, user_id)` shape) now holds the chat
THREAD id instead of the persona -- i.e. `remember_episode(persona,
thread_id, thread_id, ...)` -- so namespace
`("memories", persona, thread_id, "episodic")` scopes to exactly one
conversation, not every conversation a persona's visitors have ever had.
That is what makes real multi-thread isolation possible at all: two threads
for the same persona now write to two disjoint namespaces, so switching
threads can never bleed history across them, and DELETE /chat/threads/{id}
(app.memory.forget_thread) can wipe exactly one thread's memory and no
other's. Semantic preferences and procedural query-error memory
(app/memory/semantic.py, procedural.py) are untouched by this change -- they
stay persona-scoped, which is correct for them (a driver-safety preference or
a recurring SQL mistake pattern is a persona-wide thing, not a per-
conversation one) and nothing in this codebase calls them with a thread_id
today regardless.

`chat_threads.id` (app/services/chat_threads.py) IS the LangGraph checkpoint
thread_id AND the episodic-memory user_id -- one identifier space, not three
-- so a thread created here is immediately inspectable via
GET /threads/{id}/trace with no translation step.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import default_org_id, notifications_engine
from app.api.schemas import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatThread,
    ChatThreadCreateRequest,
    ChatThreadRenameRequest,
    PersonaId,
    ScopeOption,
)
from app.graph.supervisor import run_chat_turn
from app.llm.provider import LLMBudgetExhaustedError, ProviderNotConfiguredError
from app.memory import forget_thread, recall_episodes, remember_episode
from app.services import chat_threads, scope_options

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)


def _thread_to_schema(row: dict[str, Any]) -> ChatThread:
    return ChatThread(
        id=row["id"],
        persona=row["persona"],
        title=row["title"],
        scope_entity_type=row.get("scope_entity_type"),
        scope_entity_id=row.get("scope_entity_id"),
        created_at=row["created_at"].isoformat(),
        updated_at=row["updated_at"].isoformat(),
    )


def _episode_fallback_ts(episode: dict) -> str:
    recorded_at = episode.get("recorded_at")
    if isinstance(recorded_at, (int, float)):
        return datetime.fromtimestamp(recorded_at, tz=timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _episodes_to_messages(episodes: list[dict], thread_id: str) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    for episode in episodes:
        metadata = episode.get("metadata") or {}
        key = episode.get("key", "episode")
        fallback_ts = _episode_fallback_ts(episode)

        user_text = metadata.get("user_text")
        if user_text:
            messages.append(
                ChatMessage(
                    id=f"{key}-user",
                    role="user",
                    text=user_text,
                    thread_id=thread_id,
                    created_at=metadata.get("user_created_at") or fallback_ts,
                )
            )
        agent_text = metadata.get("agent_text")
        if agent_text:
            messages.append(
                ChatMessage(
                    id=f"{key}-agent",
                    role="agent",
                    text=agent_text,
                    thread_id=thread_id,
                    created_at=metadata.get("agent_created_at") or fallback_ts,
                )
            )
    messages.sort(key=lambda m: m.created_at)
    return messages


def _compose_question(message: str, thread: dict[str, Any]) -> str:
    """"Select something to chat with" (scope entities): when a thread has a
    scope entity set, bias the SQL agent toward it by prepending it into the
    NL question -- a prompt-composition change only, not new agent reasoning
    logic (the build brief's own framing for this feature)."""
    scope_type = thread.get("scope_entity_type")
    scope_id = thread.get("scope_entity_id")
    if scope_type and scope_id:
        return f"Regarding {scope_type} '{scope_id}': {message}"
    return message


@router.get("/chat/threads", response_model=list[ChatThread])
def list_chat_threads(persona: PersonaId) -> list[ChatThread]:
    org_id = default_org_id()
    rows = chat_threads.list_threads(notifications_engine(), org_id, persona)
    return [_thread_to_schema(row) for row in rows]


@router.post("/chat/threads", response_model=ChatThread)
def create_chat_thread(body: ChatThreadCreateRequest) -> ChatThread:
    org_id = default_org_id()
    row = chat_threads.create_thread(
        notifications_engine(),
        org_id,
        body.persona,
        scope_entity_type=body.scope_entity_type,
        scope_entity_id=body.scope_entity_id,
    )
    return _thread_to_schema(row)


@router.patch("/chat/threads/{thread_id}", response_model=ChatThread)
def rename_chat_thread(thread_id: str, body: ChatThreadRenameRequest) -> ChatThread:
    row = chat_threads.rename_thread(notifications_engine(), thread_id, body.title)
    if row is None:
        raise HTTPException(status_code=404, detail=f"chat thread {thread_id!r} not found")
    return _thread_to_schema(row)


@router.delete("/chat/threads/{thread_id}", status_code=204)
def delete_chat_thread(thread_id: str) -> None:
    engine = notifications_engine()
    row = chat_threads.get_thread(engine, thread_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"chat thread {thread_id!r} not found")
    # Delete the memory FIRST: if the thread row deleted but this failed, a
    # retry of this same DELETE would 404 before ever cleaning up the
    # now-orphaned memory. Deleting memory before the row keeps a failure
    # here retryable (get_thread would still find the row) and never leaves
    # the row disappear while memory silently survives.
    forget_thread(row["persona"], thread_id)
    chat_threads.delete_thread(engine, thread_id)


@router.get("/chat/threads/{thread_id}/messages", response_model=list[ChatMessage])
def get_thread_messages(thread_id: str) -> list[ChatMessage]:
    engine = notifications_engine()
    thread = chat_threads.get_thread(engine, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail=f"chat thread {thread_id!r} not found")
    episodes = recall_episodes(thread["persona"], thread_id, limit=200)
    return _episodes_to_messages(episodes, thread_id)


@router.get("/chat/scope-options", response_model=list[ScopeOption])
def get_scope_options(persona: PersonaId) -> list[ScopeOption]:
    org_id = default_org_id()
    rows = scope_options.list_scope_options(notifications_engine(), org_id, persona)
    return [ScopeOption(**row) for row in rows]


@router.post("/chat", response_model=ChatResponse)
def post_chat(body: ChatRequest) -> ChatResponse:
    org_id = default_org_id()
    engine = notifications_engine()

    if body.thread_id:
        thread = chat_threads.get_thread(engine, body.thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail=f"chat thread {body.thread_id!r} not found")
        if thread["persona"] != body.persona:
            raise HTTPException(status_code=400, detail="thread_id does not belong to this persona")
    else:
        # Backward-compatible smooth UX (build brief): the frontend's primary
        # flow is "create a thread, then post into it", but a caller with no
        # thread_id yet still gets a working turn -- one is created
        # implicitly, titled from this very message.
        thread = chat_threads.create_thread(engine, org_id, body.persona, title=chat_threads.default_title(body.message))

    question = _compose_question(body.message, thread)
    user_ts = datetime.now(timezone.utc)

    try:
        result = run_chat_turn(question=question, persona=body.persona, org_id=org_id, thread_id=thread["id"])
    except (LLMBudgetExhaustedError, ProviderNotConfiguredError) as exc:
        # Reuses app/llm/provider.py's existing Redis-backed daily-call-limit
        # circuit breaker for rate/cost protection -- no second one built
        # here. Surfaced as a real HTTP error (not a 200 with a fake-looking
        # answer) so the frontend can show a visible "couldn't get a
        # response, try again" state instead of a silently stuck spinner --
        # the exact failure mode already found and fixed elsewhere in this
        # app today (see docker-compose.yml's env_file comment history).
        logger.warning("chat turn blocked for thread %s: %s", thread["id"], exc)
        raise HTTPException(status_code=503, detail="The assistant is temporarily unavailable. Please try again shortly.") from exc
    except Exception as exc:  # noqa: BLE001 - any other LLM/graph failure must not hang the request
        logger.exception("chat turn failed for thread %s", thread["id"])
        raise HTTPException(status_code=502, detail="Couldn't get a response for that. Please try again.") from exc

    agent_ts = datetime.now(timezone.utc)
    thread_id = result["thread_id"]
    answer = result.get("answer") or "I couldn't produce a confident, grounded answer for that yet."

    agent_message = ChatMessage(
        id=f"{thread_id}-agent-{int(agent_ts.timestamp() * 1000)}",
        role="agent",
        text=answer,
        thread_id=thread_id,
        created_at=agent_ts.isoformat(),
    )

    remember_episode(
        body.persona,
        thread_id,
        thread_id,
        f"User asked: {body.message}\nAgent answered: {answer}",
        metadata={
            "user_text": body.message,
            "agent_text": answer,
            "user_created_at": user_ts.isoformat(),
            "agent_created_at": agent_ts.isoformat(),
        },
    )
    chat_threads.touch_thread(engine, thread_id)

    return ChatResponse(message=agent_message)
