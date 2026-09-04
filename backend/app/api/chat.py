"""GET /api/chat/history?persona=<id> and POST /api/chat (plan §11, TM2/LM2/TH4).

No login exists in this build, so there is no browser/session id the
frontend sends today (checked frontend/src/api/apiClient.ts and
state/AppStateContext.tsx -- neither attaches a cookie or client id header).
Per the build brief's own latitude ("your call on the simplest mechanism"),
this uses the PERSONA ITSELF as app.memory's `user_id`: every visitor acting
as e.g. "transport_manager" shares one conversational memory namespace for
that role. That's the simplest thing that (a) satisfies LangMem's namespace
shape (persona_id, user_id) without inventing a second, redundant identifier
that always equals persona anyway, and (b) matches how this whole app already
works with no per-person accounts -- a role, not a person, is the unit of
identity. A real per-browser id (cookie/header) is a drop-in swap later
without touching call sites, since they only ever pass persona_id/user_id
through (see app/memory/namespaces.py's own docstring).

Each turn is persisted as ONE episodic memory entry carrying BOTH the user's
question and the agent's answer in `metadata` (not two separate episodes),
so GET /chat/history can reconstruct the exact user/agent pairing and
ordering without guessing which reply answered which question.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from app.api.deps import default_org_id
from app.api.schemas import ChatMessage, ChatRequest, ChatResponse, PersonaId
from app.graph.supervisor import run_chat_turn
from app.memory import recall_episodes, remember_episode

router = APIRouter(tags=["chat"])


def _episode_fallback_ts(episode: dict) -> str:
    recorded_at = episode.get("recorded_at")
    if isinstance(recorded_at, (int, float)):
        return datetime.fromtimestamp(recorded_at, tz=timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


@router.get("/chat/history", response_model=list[ChatMessage])
def get_chat_history(persona: PersonaId) -> list[ChatMessage]:
    episodes = recall_episodes(persona, persona, limit=50)
    messages: list[ChatMessage] = []
    for episode in episodes:
        metadata = episode.get("metadata") or {}
        thread_id = episode.get("thread_id")
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


@router.post("/chat", response_model=ChatResponse)
def post_chat(body: ChatRequest) -> ChatResponse:
    org_id = default_org_id()
    user_ts = datetime.now(timezone.utc)

    result = run_chat_turn(question=body.message, persona=body.persona, org_id=org_id)

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
        body.persona,
        thread_id,
        f"User asked: {body.message}\nAgent answered: {answer}",
        metadata={
            "user_text": body.message,
            "agent_text": answer,
            "user_created_at": user_ts.isoformat(),
            "agent_created_at": agent_ts.isoformat(),
        },
    )

    return ChatResponse(message=agent_message)
