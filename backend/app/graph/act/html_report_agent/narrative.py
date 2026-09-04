"""LLM-narrative mode for the HTML report (plan §2: "layer on top ... use
get_chat_model() to generate the prose narrative sections ... from the
structured input, then slot that into the same HTML template").

Called by generator.py *after* the static/templated report already renders
correctly on its own (build order: static mode first, this second). Any
failure here -- LLMBudgetExhaustedError/ProviderNotConfiguredError from
app.llm.provider, a malformed response, a network error -- degrades to
`static_narrative.generate_static_narrative()` rather than failing report
generation outright: a forwardable report with a templated narrative beats no
report at all.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

# Sarvam (the active default provider, plan §5) emits hidden chain-of-thought
# in a separate `reasoning_content` field before the actual answer in
# `content` -- confirmed live during build verification. Observed reasoning
# length for this prompt varies run to run from ~1400 to 2048+ tokens (LLM
# sampling variance, not a deterministic cost); when it runs long enough to
# exhaust the budget, the API returns finish_reason="length" with `content`
# EMPTY rather than truncated-but-present. 12288 gives generous headroom
# without eliminating the possibility entirely -- this is exactly why
# generate_llm_narrative() degrades to the static narrative on any failure
# rather than assuming success (plan §14: "Sarvam isn't a frontier reasoning
# model -- spot-check root-cause synthesis quality").
DEFAULT_NARRATIVE_MAX_TOKENS = 12288

_SYSTEM_PROMPT = (
    "You are drafting the narrative sections of an executive transportation-operations "
    "report for MoveInSync, an enterprise employee-transportation platform. Write in a "
    "concise, factual, board-ready tone -- no hype, no emoji, no markdown formatting. "
    "Ground every claim only in the structured facts provided; never invent numbers or "
    "details not present in the input. Respond with ONLY a JSON object of the shape "
    '{"exec_summary": "...", "why_it_matters": "..."} and nothing else. '
    "exec_summary: 2-4 sentences summarizing the period's key findings. "
    "why_it_matters: 2-4 sentences on business impact and recommended focus, tied "
    "directly to the supplied metrics and decision items."
)


def generate_llm_narrative(
    items: list[dict[str, Any]],
    metrics: dict[str, Any],
    persona: str,
    period_label: str,
    *,
    llm: BaseChatModel | None = None,
) -> dict[str, str] | None:
    """Returns {"exec_summary": ..., "why_it_matters": ...} or None on any
    failure (caller falls back to the static narrative)."""
    try:
        if llm is None:
            from app.llm import get_chat_model

            llm = get_chat_model(max_tokens=DEFAULT_NARRATIVE_MAX_TOKENS)

        facts = {
            "persona": persona,
            "period_label": period_label,
            "metrics": metrics,
            "decisions": [
                {
                    "summary": it.get("summary"),
                    "root_cause": it.get("root_cause"),
                    "recommendation": it.get("recommendation"),
                    "confidence": it.get("confidence"),
                    "needs_human_signoff": it.get("needs_human_signoff"),
                }
                for it in items
            ],
        }
        response = llm.invoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(content=json.dumps(facts, default=str)),
            ]
        )
        raw_text = response.content if isinstance(response.content, str) else str(response.content)
        parsed = _extract_json(raw_text)
        if not parsed or "exec_summary" not in parsed or "why_it_matters" not in parsed:
            logger.warning("LLM narrative response missing expected keys, falling back to static narrative")
            return None
        return {"exec_summary": str(parsed["exec_summary"]), "why_it_matters": str(parsed["why_it_matters"])}
    except Exception:
        logger.exception("LLM narrative generation failed, falling back to static narrative")
        return None


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
