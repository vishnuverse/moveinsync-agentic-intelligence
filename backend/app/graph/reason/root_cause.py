"""synthesize_root_cause (plan §2 Reason paragraph, task item 3).

Combines a Signal + its ImpactContext (+ optional SQLAgentResult for
internal-data supporting detail, and/or a research_agent benchmark) into a
final ReasonDecision via one LLM call. See prompts.py for why this is
prompt-and-parse rather than tool-calling.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.graph.reason import prompts
from app.graph.reason.state import ImpactContext, ReasonDecision
from app.graph.reason.sql_agent import SQLAgentResult
from app.graph.sense.state import Signal

logger = logging.getLogger(__name__)

_JSON_FENCE_RE = re.compile(r"```json\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_BRACE_RE = re.compile(r"\{.*\}", re.DOTALL)

_VALID_PERSONAS = ("transport_manager", "line_manager", "transport_head")

# Bound with an explicit max_tokens for the same reason sql_agent/nodes.py
# binds one on its own LLM calls: Sarvam's default output cap (observed at
# 2048 tokens elsewhere, but empirically even lower / near-zero for some
# prompts during this module's own verification -- finish_reason="length"
# with zero content emitted) can truncate the JSON response to nothing
# before it starts. A roomier explicit budget avoids trusting whatever the
# injected llm happens to default to.
DEFAULT_ROOT_CAUSE_MAX_TOKENS = 1024

# Signal types/severities that force sign-off regardless of what the LLM
# decides -- a defensive floor, not a replacement for the LLM's judgement,
# enforcing the plan's explicit "safety-critical or customer/leadership-
# facing" rule (task item 3) even if a non-frontier model's own judgement
# call is inconsistent.
_FORCE_SIGNOFF_SIGNAL_TYPES = {"incident"}
_FORCE_SIGNOFF_SEVERITIES = {"critical"}


def _extract_json(text: str) -> dict[str, Any] | None:
    fenced = _JSON_FENCE_RE.findall(text)
    candidates = [fenced[-1]] if fenced else []
    brace_match = _BRACE_RE.search(text)
    if brace_match:
        candidates.append(brace_match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _format_signal_block(signal: Signal) -> str:
    return (
        f"type: {signal.signal_type}\n"
        f"entity: {signal.entity_type} {signal.entity_id}\n"
        f"severity: {signal.severity}\n"
        f"summary: {signal.summary}\n"
        f"raw_metric: {json.dumps(signal.raw_metric, default=str)}\n"
        f"context: {json.dumps(signal.context, default=str)}"
    )


def _format_impact_block(impact_context: ImpactContext) -> str:
    return (
        f"trend_direction: {impact_context['trend_direction']}\n"
        f"severity_band: {impact_context['severity_band']}\n"
        f"business_impact: {impact_context['business_impact']}\n"
        f"comparison_baseline: {json.dumps(impact_context['comparison_baseline'], default=str)}"
    )


def _format_sql_block(sql_result: SQLAgentResult | None) -> str:
    if sql_result is None:
        return ""
    return (
        "Supporting internal-data detail (from the SQL agent):\n"
        f"question asked: {sql_result['question']}\n"
        f"answer: {sql_result['answer']}\n"
        f"generated_sql: {sql_result.get('generated_sql') or '(none)'}\n\n"
    )


def _infer_persona_fallback(signal: Signal | None) -> str:
    if signal is None:
        return "transport_manager"
    if signal.entity_type == "employee":
        return "line_manager"
    if signal.signal_type in ("cost_divergence", "emissions_over_target"):
        return "transport_head"
    return "transport_manager"


def _build_supporting_evidence(
    signal: Signal | None,
    sql_result: SQLAgentResult | None,
    benchmark: dict[str, Any] | None = None,
) -> list[str]:
    """Assembled deterministically from what's already on hand, rather than
    trusting the LLM to faithfully reproduce exact SQL/benchmark strings it
    was only shown as prose -- the same "don't let the model restate what we
    already have verbatim" caution sql_agent's synthesize_answer relies on
    the raw row data for, not the model's own recollection of it."""

    evidence: list[str] = []
    if signal is not None:
        evidence.append(f"signal[{signal.source}]: {signal.summary}")
    if sql_result is not None and sql_result.get("generated_sql"):
        evidence.append(f"sql_agent query: {sql_result['generated_sql']}")
    if sql_result is not None and sql_result.get("answer"):
        evidence.append(f"sql_agent answer: {sql_result['answer']}")
    if benchmark is not None and benchmark.get("benchmark_source"):
        evidence.append(f"benchmark: {benchmark['benchmark_source']}")
    return evidence


def _fallback_decision(
    signal: Signal | None,
    impact_context: ImpactContext,
    sql_result: SQLAgentResult | None,
    benchmark: dict[str, Any] | None,
    reason: str,
) -> ReasonDecision:
    """Fail closed -- same philosophy as sql_agent's fail_closed node: never
    silently guess at a root cause the model couldn't actually produce."""

    logger.warning("root cause synthesis fell back to a safe default: %s", reason)
    return ReasonDecision(
        summary=impact_context["business_impact"],
        root_cause="Could not confidently determine a root cause automatically -- needs manual review.",
        recommendation="A human should review the underlying data before any action is taken.",
        confidence=0.2,
        needs_human_signoff=True,
        target_persona=_infer_persona_fallback(signal),
        supporting_evidence=_build_supporting_evidence(signal, sql_result, benchmark),
    )


def synthesize_root_cause(
    signal: Signal | None,
    impact_context: ImpactContext,
    sql_result: SQLAgentResult | None = None,
    benchmark: dict[str, Any] | None = None,
    *,
    llm: BaseChatModel | None = None,
) -> ReasonDecision:
    """One LLM call reasoning about WHY, producing a ReasonDecision with an
    explicit needs_human_signoff flag (task item 3). Falls back to a safe,
    sign-off-required default if the model's response can't be parsed --
    never silently guesses."""

    if llm is None:
        from app.llm import get_chat_model

        llm = get_chat_model()
    bound_llm = llm.bind(max_tokens=DEFAULT_ROOT_CAUSE_MAX_TOKENS)

    signal_block = (
        _format_signal_block(signal)
        if signal is not None
        else "(no sense-stage signal -- this run originated from a direct question)"
    )
    messages = [
        SystemMessage(content=prompts.ROOT_CAUSE_SYSTEM_PROMPT),
        HumanMessage(
            content=prompts.ROOT_CAUSE_USER_TEMPLATE.format(
                signal_block=signal_block,
                impact_block=_format_impact_block(impact_context),
                sql_block=_format_sql_block(sql_result),
            )
        ),
    ]

    response = bound_llm.invoke(messages)
    content = response.content if isinstance(response.content, str) else str(response.content)
    was_truncated = response.response_metadata.get("finish_reason") == "length"
    parsed = None if was_truncated else _extract_json(content)

    if parsed is None:
        reason = (
            "LLM response hit the token limit before any JSON was emitted"
            if was_truncated
            else "LLM response did not contain parseable JSON"
        )
        return _fallback_decision(signal, impact_context, sql_result, benchmark, reason)

    try:
        summary = str(parsed["summary"])
        root_cause = str(parsed["root_cause"])
        recommendation = str(parsed["recommendation"])
        confidence = max(0.0, min(1.0, float(parsed["confidence"])))
        needs_human_signoff = bool(parsed["needs_human_signoff"])
        target_persona = str(parsed.get("target_persona", "")).strip()
        if target_persona not in _VALID_PERSONAS:
            target_persona = _infer_persona_fallback(signal)
    except (KeyError, TypeError, ValueError) as exc:
        return _fallback_decision(
            signal, impact_context, sql_result, benchmark, f"missing/invalid field in LLM JSON: {exc}"
        )

    if signal is not None and (
        signal.signal_type in _FORCE_SIGNOFF_SIGNAL_TYPES or signal.severity in _FORCE_SIGNOFF_SEVERITIES
    ):
        needs_human_signoff = True

    return ReasonDecision(
        summary=summary,
        root_cause=root_cause,
        recommendation=recommendation,
        confidence=confidence,
        needs_human_signoff=needs_human_signoff,
        target_persona=target_persona,
        supporting_evidence=_build_supporting_evidence(signal, sql_result, benchmark),
    )
