"""communication_drafter node (plan §2 Act paragraph, item 3).

Drafts driver/vendor/leadership message text from a ReasonDecision. Purely a
drafting step -- no send, no DB write -- so it is inherently safe to replay
before interrupt_gate: the plan's idempotency requirement ("no side effects
... may happen non-idempotently before this interrupt") is satisfied here by
construction, since there is no side effect to make idempotent.

Sarvam path (plan §2: "if the target audience is a driver/vendor likely to
prefer a regional language, use Sarvam's translation/TTS capabilities as a
separate dedicated tool call"): checked backend/app/llm/provider.py --
confirmed it exposes only get_chat_model() for general chat/text, no
dedicated Sarvam TTS/translate tool-node exists yet. Per the build brief this
is a stretch feature, not core path: when the audience looks like a
driver/vendor with a non-English language preference, this node still drafts
in English via the general chat LLM and attaches a `communication_channel_note`
flagging the follow-up rather than blocking on building Sarvam speech
integration.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from .state import ActState

# See nodes.py's module-level NOTE: `Optional[RunnableConfig]`, not
# `RunnableConfig | None`, is required for LangGraph to auto-inject the live
# config (and therefore configurable.thread_id) into this node.

logger = logging.getLogger(__name__)

_DRIVER_VENDOR_AUDIENCES = {"driver", "vendor"}

# See html_report_agent/narrative.py's DEFAULT_NARRATIVE_MAX_TOKENS docstring --
# Sarvam (active default provider) spends its budget on a hidden
# reasoning_content field before content; too low a max_tokens returns EMPTY
# content with finish_reason="length". This node already falls back to a
# static templated draft on any failure, so a generous-but-imperfect budget
# is the right tradeoff over chasing a guarantee the API doesn't offer.
DEFAULT_DRAFT_MAX_TOKENS = 12288

_SYSTEM_PROMPT = (
    "You draft short, professional operational messages for MoveInSync, an enterprise "
    "employee-transportation platform. Write only the message body -- no subject line, "
    "no markdown, no bracketed placeholders. Keep it under 120 words. Match tone to "
    "audience: drivers/vendors get plain, direct, actionable language; leadership gets "
    "a concise, business-framed note."
)


def _static_fallback_draft(decision: dict[str, Any], audience: str) -> str:
    summary = decision.get("summary") or "An operational item requires your attention."
    recommendation = decision.get("recommendation") or ""
    if audience in _DRIVER_VENDOR_AUDIENCES:
        body = f"{summary} Please action: {recommendation}" if recommendation else summary
    else:
        body = f"{summary} Recommended next step: {recommendation}" if recommendation else summary
    return body.strip()


def communication_drafter(state: ActState, config: Optional[RunnableConfig] = None) -> dict[str, Any]:
    decision = state.get("decision", {})
    audience = state.get("audience") or "leadership"
    language_pref = state.get("audience_language_pref")

    draft: str | None = None
    try:
        from app.llm import get_chat_model

        llm = get_chat_model(max_tokens=DEFAULT_DRAFT_MAX_TOKENS)
        response = llm.invoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(
                    content=(
                        f"Audience: {audience}\n"
                        f"Summary: {decision.get('summary', '')}\n"
                        f"Root cause: {decision.get('root_cause', '')}\n"
                        f"Recommendation: {decision.get('recommendation', '')}\n"
                    )
                ),
            ]
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        draft = content.strip() or None
    except Exception:
        logger.exception("communication_drafter: LLM draft failed, falling back to static draft")
        draft = None

    if not draft:
        draft = _static_fallback_draft(decision, audience)

    channel_note = None
    if (
        audience in _DRIVER_VENDOR_AUDIENCES
        and language_pref
        and language_pref.strip().lower() not in ("", "en", "english")
    ):
        channel_note = (
            f"Audience prefers '{language_pref}'. No dedicated Sarvam translate/TTS tool-node "
            "exists yet (app.llm.provider currently only exposes get_chat_model() for text) -- "
            "drafted in English via the general chat LLM. Natural follow-up: add a dedicated "
            "Sarvam translate/TTS tool-node call (kept separate from the general chat LLM per "
            "plan §2) to localize this draft, and optionally synthesize audio, before send."
        )

    return {"communication_draft": draft, "communication_channel_note": channel_note}
