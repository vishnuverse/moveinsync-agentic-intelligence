"""Node implementations for the SQL agent cluster.

Official LangGraph SQL-agent tutorial pattern, implemented as plain sequential
node functions (not a tool-calling ReAct loop) so the retry cap and error log
are counted deterministically rather than left to the model's own tool-call
discipline -- important since the default LLM (Sarvam) is not a frontier
reasoning model (plan §5/§14) and a ReAct loop is the more failure-prone shape
for it.

Nodes are methods on SQLAgentNodes so they can close over the SQLDatabase/LLM
without relying on LangGraph's config-passing, keeping each method a clean
`(state) -> partial state update` function per plan §4 ("nodes are pure
functions").
"""

from __future__ import annotations

import re
from typing import Any

from langchain_community.utilities import SQLDatabase
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from . import prompts
from .security import CheckedQuery, SQLSecurityError, SQLSyntaxError, enforce_select_only
from .state import DEFAULT_ROW_LIMIT, MAX_SQL_RETRIES, SQLAgentState

_SQL_FENCE_RE = re.compile(r"```sql\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_BARE_SELECT_RE = re.compile(r"(SELECT\b.*)", re.IGNORECASE | re.DOTALL)

_TRUNCATED_SENTINEL = "__GENERATION_TRUNCATED__"
# Integrator's note (supervisor-assembly agent, wiring reason/ end-to-end):
# bumped from 4096/1024. Confirmed live against the default provider (Sarvam
# sarvam-105b) that it enforces its own server-side completion cap around
# ~2048 tokens regardless of a higher client-requested max_tokens (observed
# `finish_reason: "length"` with `completion_tokens: 2048` even when 16384 was
# requested) -- so this constant cannot fully eliminate GenerationTruncated
# for a schema-heavy question against this model/prompt combination (the full
# multi-table DDL+sample-rows schema context here runs ~10K prompt tokens,
# and the "think step-by-step" instruction can burn the entire ~2048-token
# completion budget before reaching the SQL fence on a non-trivial
# aggregate question). Simple/few-table questions still succeed on the first
# attempt (verified live). Raising this default is still correct -- it's a
# real ceiling for less output-capped providers/models -- but a genuine
# GenerationTruncated failure on this specific model+schema-size combination
# is a known limitation, not something this constant alone fixes; the
# existing capped-retry + fail_closed path is the correct mitigation and
# already handles it gracefully (see build report).
DEFAULT_GENERATE_MAX_TOKENS = 8192
DEFAULT_ANSWER_MAX_TOKENS = 2048


def _extract_sql(text: str) -> str:
    """Pull the SQL out of an LLM response that (per prompt) wraps it in ```sql fences.

    Falls back to a bare SELECT-onward scan for models that ignore the fence
    instruction -- keeps the pipeline resilient to a non-frontier model's
    formatting slips rather than treating "no fence" as an automatic failure.
    """
    fenced = _SQL_FENCE_RE.findall(text)
    if fenced:
        return fenced[-1].strip().rstrip(";").strip()
    bare = _BARE_SELECT_RE.search(text)
    if bare:
        return bare.group(1).strip().rstrip(";").strip()
    return text.strip().rstrip(";").strip()


def _detect_out_of_scope(text: str) -> str | None:
    """Return the short reason if the model declared the question out of scope
    (a line beginning with prompts.OUT_OF_SCOPE_MARKER), else None.

    Scans the first few non-empty lines rather than only the very first char:
    a non-frontier model may prepend a stray blank line or a "Reasoning:"
    false-start before the marker. To avoid a false positive on a legitimate
    query that merely mentions the phrase in prose, only lines that *start*
    with the marker (after stripping) count, and only within the opening lines
    before any ```sql fence."""
    for raw_line in text.strip().splitlines()[:5]:
        line = raw_line.strip()
        if not line:
            continue
        if line.upper().startswith(prompts.OUT_OF_SCOPE_MARKER):
            reason = line[len(prompts.OUT_OF_SCOPE_MARKER):].strip()
            return reason or "the question is outside this database's scope"
        if line.startswith("```"):
            break
    return None


def _format_rows_preview(rows: list[dict[str, Any]], *, limit: int = 20) -> str:
    if not rows:
        return "(no rows returned)"
    preview = rows[:limit]
    columns = list(preview[0].keys())
    lines = ["\t".join(columns)]
    for row in preview:
        lines.append("\t".join(str(row.get(c, "")) for c in columns))
    return "\n".join(lines)


class SQLAgentNodes:
    def __init__(
        self,
        db: SQLDatabase,
        llm: BaseChatModel,
        *,
        org_id: str,
        row_limit: int = DEFAULT_ROW_LIMIT,
        generate_max_tokens: int = DEFAULT_GENERATE_MAX_TOKENS,
        answer_max_tokens: int = DEFAULT_ANSWER_MAX_TOKENS,
    ) -> None:
        self.db = db
        self.llm = llm
        self.org_id = org_id
        self.row_limit = row_limit
        # Bound with an explicit max_tokens: the chain-of-thought prompt (#3)
        # asks for step-by-step reasoning *before* the SQL fence, and a
        # provider's low default output cap can truncate the response before
        # the SQL ever appears (observed against Sarvam's default 2048-token
        # cap during verification) -- bind a roomier budget defensively
        # rather than trusting whatever the injected llm happens to default to.
        self._generate_llm = llm.bind(max_tokens=generate_max_tokens)
        self._answer_llm = llm.bind(max_tokens=answer_max_tokens)

    # -- list_tables -----------------------------------------------------

    def list_tables(self, state: SQLAgentState) -> dict[str, Any]:
        table_names = self.db.get_usable_table_names()
        return {"table_names": list(table_names)}

    # -- get_schema --------------------------------------------------------
    # SQLDatabase is constructed with sample_rows_in_table_info set (see
    # subgraph.py), so get_table_info() already returns full CREATE TABLE DDL
    # plus real sample rows per table in one call -- hardening requirement #1.

    def get_schema(self, state: SQLAgentState) -> dict[str, Any]:
        schema_context = self.db.get_table_info(table_names=state["table_names"])
        return {"schema_context": schema_context}

    # -- generate_query ------------------------------------------------

    def generate_query(self, state: SQLAgentState) -> dict[str, Any]:
        system = prompts.SYSTEM_PROMPT.format(
            org_id=self.org_id,
            schema_context=state["schema_context"],
            scope_context=prompts.build_scope_context(),
        )
        messages = [SystemMessage(content=system)]

        errors = state.get("query_error_log") or []
        if errors:
            messages.append(
                HumanMessage(
                    content=prompts.RETRY_PROMPT_TEMPLATE.format(
                        previous_sql=state.get("generated_sql") or "(none)",
                        errors="\n".join(f"- {e}" for e in errors),
                    )
                )
            )
        else:
            messages.append(HumanMessage(content=f"Question: {state['question']}"))

        response = self._generate_llm.invoke(messages)
        content = response.content if isinstance(response.content, str) else str(response.content)
        was_truncated = response.response_metadata.get("finish_reason") == "length"

        # Scope guard (reuses this same generation, no extra LLM round-trip):
        # if the model judged the question unanswerable from the contract's
        # business entities, it emits an OUT_OF_SCOPE line instead of SQL.
        # Detect it here and short-circuit to the decline node. Only honored on
        # a non-truncated response (a truncated one could coincidentally start
        # with the marker mid-thought) and on the first attempt (an out-of-scope
        # verdict has nothing to retry).
        if not was_truncated and not errors:
            reason = _detect_out_of_scope(content)
            if reason is not None:
                return {"out_of_scope": True, "out_of_scope_reason": reason}

        if was_truncated:
            # A "length" finish_reason means the API cut the response off
            # mid-stream -- any SQL text extracted from it (fenced or via the
            # bare-SELECT fallback) is by definition incomplete and must not
            # be trusted, even if it superficially looks non-empty. Treat it
            # as a distinct, sentinel failure rather than letting garbled SQL
            # reach check_query's sqlglot parser as a generic syntax error.
            sql = _TRUNCATED_SENTINEL
        else:
            sql = _extract_sql(content)
        return {
            "generated_sql": sql,
            "messages": [HumanMessage(content=state["question"])] if not errors else [],
        }

    @staticmethod
    def route_after_generate(state: SQLAgentState) -> str:
        """Conditional-edge router after generate_query: an out-of-scope
        verdict goes straight to the decline terminal node (bypassing
        check/run/answer), everything else proceeds to the SELECT guard.
        Mirrors the route_after_check/fail_closed shape."""
        return "decline" if state.get("out_of_scope") else "check"

    # -- decline (out-of-scope terminal) ---------------------------------

    def decline(self, state: SQLAgentState) -> dict[str, Any]:
        """Terminal node for a question the model judged unanswerable from the
        contract's business entities. Returns a graceful, figure-free answer
        (no SQL was run, so nothing to summarize) and marks the run done. This
        is a *successful* handling of an out-of-scope ask, not a pipeline
        failure -- success=True, generated_sql stays empty."""
        reason = state.get("out_of_scope_reason") or "the question is outside this database's scope"
        message = prompts.OUT_OF_SCOPE_ANSWER.format(reason=reason)
        return {
            "final_answer": message,
            "generated_sql": "",
            "success": True,
            "done": True,
            "messages": [AIMessage(content=message)],
        }

    # -- check_query -----------------------------------------------------

    def check_query(self, state: SQLAgentState) -> dict[str, Any]:
        sql = state["generated_sql"]
        if sql == _TRUNCATED_SENTINEL:
            return {
                "generated_sql": "",
                "query_error_log": [
                    *state["query_error_log"],
                    "GenerationTruncated: response hit the token limit before the SQL block was "
                    "emitted -- keep the Reasoning section shorter and go straight to the SQL.",
                ],
                "sql_retry_count": state["sql_retry_count"] + 1,
                "last_step_ok": False,
            }
        try:
            checked: CheckedQuery = enforce_select_only(sql, row_limit=self.row_limit)
        except (SQLSyntaxError, SQLSecurityError) as exc:
            return {
                "query_error_log": [*state["query_error_log"], f"{type(exc).__name__}: {exc}"],
                "sql_retry_count": state["sql_retry_count"] + 1,
                "last_step_ok": False,
            }
        return {"generated_sql": checked.safe_sql, "last_step_ok": True}

    @staticmethod
    def route_after_check(state: SQLAgentState) -> str:
        """Conditional-edge router after check_query."""
        if state["last_step_ok"]:
            return "proceed"
        if state["sql_retry_count"] >= MAX_SQL_RETRIES:
            return "fail_closed"
        return "retry"

    # -- run_query ---------------------------------------------------------

    def run_query(self, state: SQLAgentState) -> dict[str, Any]:
        sql = state["generated_sql"]
        try:
            # db._execute is the underlying call db.run()/run_no_throw() both
            # delegate to; used directly (rather than db.run()) because run()
            # always collapses the result to a stringified repr, and we need
            # real row dicts for the row-count guard and the answer prompt.
            rows = self.db._execute(sql, fetch="all")  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - surfaced into the retry loop, not swallowed
            error = f"DBExecutionError: {exc}"
            return {
                "query_error_log": [*state["query_error_log"], error],
                "sql_retry_count": state["sql_retry_count"] + 1,
                "result_rows": None,
                "last_step_ok": False,
            }

        capped_rows = rows[: self.row_limit]
        return {"result_rows": capped_rows, "last_step_ok": True}

    @staticmethod
    def route_after_run(state: SQLAgentState) -> str:
        if state["last_step_ok"]:
            return "proceed"
        if state["sql_retry_count"] >= MAX_SQL_RETRIES:
            return "fail_closed"
        return "retry"

    # -- synthesize_answer -------------------------------------------------

    def synthesize_answer(self, state: SQLAgentState) -> dict[str, Any]:
        rows = state["result_rows"] or []
        messages = [
            SystemMessage(content=prompts.ANSWER_SYSTEM_PROMPT),
            HumanMessage(
                content=prompts.ANSWER_USER_TEMPLATE.format(
                    question=state["question"],
                    sql=state["generated_sql"],
                    row_count=len(rows),
                    rows_preview=_format_rows_preview(rows),
                )
            ),
        ]
        response = self._answer_llm.invoke(messages)
        answer = response.content if isinstance(response.content, str) else str(response.content)
        return {
            "final_answer": answer.strip(),
            "success": True,
            "done": True,
            "messages": [AIMessage(content=answer.strip())],
        }

    # -- fail_closed ---------------------------------------------------

    def fail_closed(self, state: SQLAgentState) -> dict[str, Any]:
        message = prompts.FAIL_CLOSED_MESSAGE.format(
            retries=state["sql_retry_count"],
            errors="; ".join(state["query_error_log"]) or "(none logged)",
        )
        return {
            "final_answer": message,
            "success": False,
            "done": True,
            "messages": [AIMessage(content=message)],
        }
