"""State shape for the SQL agent cluster (plan §4 Reason subgraph, §12 step 6).

TypedDict per the plan's "TypedDict inside the graph, Pydantic only at API
boundaries" convention. This subgraph is standalone and callable on its own
(see subgraph.run_sql_agent) so a parent reason subgraph can embed it without
needing to match its internal state shape.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

MAX_SQL_RETRIES = 3
DEFAULT_ROW_LIMIT = 200
DEFAULT_STATEMENT_TIMEOUT_MS = 8_000


class SQLAgentResult(TypedDict):
    """The public, judge-facing output of run_sql_agent()."""

    question: str
    answer: str
    generated_sql: str | None
    success: bool
    sql_retry_count: int
    query_error_log: list[str]
    rows_returned: int | None


class SQLAgentState(TypedDict):
    """Internal graph state threaded through list_tables -> ... -> run_query."""

    messages: Annotated[list[AnyMessage], add_messages]
    question: str
    org_id: str
    table_names: list[str]
    schema_context: str
    generated_sql: str
    sql_retry_count: int
    query_error_log: list[str]
    result_rows: list[dict[str, Any]] | None
    final_answer: str
    success: bool
    done: bool
    last_step_ok: bool
    # Set by generate_query when the model judges the question unanswerable
    # from the contract's business entities (it emits an OUT_OF_SCOPE line
    # instead of SQL). Routes the subgraph straight to the `decline` terminal
    # node, bypassing check/run/answer. total=False semantics: absent == not
    # out of scope (SQLAgentState is a plain TypedDict, so callers read it via
    # state.get("out_of_scope")).
    out_of_scope: bool
    out_of_scope_reason: str
