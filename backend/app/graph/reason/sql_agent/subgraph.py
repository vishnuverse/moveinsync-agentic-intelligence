"""Compiled SQL agent subgraph + the run_sql_agent() convenience callable.

Embedding contract for the parent reason subgraph (plan §4/§11):
    from app.graph.reason.sql_agent import run_sql_agent
    result = run_sql_agent("Which route has the worst average delay?", thread_context={...})
    result["generated_sql"], result["answer"], result["success"], ...

build_sql_agent_subgraph() is exposed separately for a parent graph that wants
to compose this as an actual LangGraph subgraph node rather than call it as a
black-box function -- both shapes are covered so the embedding agent can pick
whichever fits the parent graph's state.
"""

from __future__ import annotations

import functools
import os
from typing import Any

from langchain_community.utilities import SQLDatabase
from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, StateGraph

from .nodes import SQLAgentNodes
from .state import (
    DEFAULT_ROW_LIMIT,
    DEFAULT_STATEMENT_TIMEOUT_MS,
    SQLAgentResult,
    SQLAgentState,
)

DEFAULT_ORG_ID = "moveinsync-demo"


def _normalize_pg_url(database_url: str) -> str:
    """Force the psycopg3 SQLAlchemy dialect.

    backend/.env ships a bare postgresql:// DSN; SQLAlchemy defaults that to
    psycopg2, which this module does not depend on (psycopg3 is what's
    installed). Rewriting the scheme here keeps this module self-contained
    without needing backend/config or backend/app/llm to agree on a driver.
    """
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url[len("postgresql://") :]
    return database_url


def _active_schema() -> str | None:
    """Derives the active contract's schema from the `trip` entity's table
    name (split on `.`), the same source of truth used everywhere else in
    this codebase, so this never needs a second config to keep in sync."""
    try:
        from app.contracts import get_contract

        table = get_contract().entity("trip").table
    except Exception:
        return None
    return table.split(".", 1)[0] if "." in table else None


def _contract_table_names() -> list[str] | None:
    """BUGFIX (found live: with search_path set but no include_tables filter,
    SQLAlchemy's `get_table_names(schema=None)` returned an unqualified,
    unlabeled UNION of every schema's tables -- both `mis.incident` and the
    old synthetic `public.safety_incidents` showed up in the same flat list
    as bare `incident` / `safety_incidents`, with nothing telling the LLM
    which one is real. It picked `safety_incidents` -- a plausible-sounding
    guess, not a grounded answer, and the mandatory "grounded, not
    hallucinated" story broke silently. Restricting to exactly the contract's
    own entity tables (unqualified; `search_path` resolves them correctly)
    removes the ambiguity at the source, and as a side effect keeps
    checkpointer/store/pipeline_runs internals out of the LLM's schema
    entirely -- it only ever sees the tables it's actually meant to answer
    questions about."""
    try:
        from app.contracts import get_contract

        contract = get_contract()
        return sorted({contract.entity(name).table.split(".", 1)[-1] for name in contract.entity_names})
    except Exception:
        return None


@functools.lru_cache(maxsize=8)
def _build_database(database_url: str, statement_timeout_ms: int) -> SQLDatabase:
    schema = _active_schema()
    # Deliberately NOT passing schema= to SQLDatabase.from_uri(): langchain_
    # community's SQLDatabase._execute() unconditionally runs
    # `SET search_path TO %s` as a driver-parameterized statement whenever
    # self._schema is set (postgresql branch) -- Postgres's SET does not
    # accept bind parameters, so that raises `SyntaxError: at or near "$1"`
    # on every single query execution once schema= is set (found live,
    # confirmed by reading langchain_community's actual installed source).
    # search_path is set at the CONNECTION level instead (same connect_args
    # mechanism statement_timeout already uses below), which fixes query
    # EXECUTION -- but get_table_names(schema=None) still ignores search_path
    # for introspection (confirmed live: it returns a flat, unlabeled union
    # of every schema's tables, e.g. both bare `incident` and bare
    # `safety_incidents`), so include_tables= is what actually disambiguates
    # what the LLM sees -- see _contract_table_names()'s own docstring.
    options = f"-c statement_timeout={statement_timeout_ms}"
    if schema:
        options += f" -c search_path={schema},public"
    return SQLDatabase.from_uri(
        _normalize_pg_url(database_url),
        include_tables=_contract_table_names(),
        sample_rows_in_table_info=3,
        engine_args={
            "connect_args": {"options": options},
            "pool_pre_ping": True,
        },
    )


def _default_llm() -> BaseChatModel:
    try:
        from app.llm.provider import get_chat_model
    except ImportError as exc:
        raise RuntimeError(
            "No llm was passed to run_sql_agent() and app.llm.provider.get_chat_model "
            "is not importable yet. Either pass llm= explicitly, or wait for "
            "backend/app/llm/provider.py to land (plan §5)."
        ) from exc
    return get_chat_model()


def build_sql_agent_subgraph(
    db: SQLDatabase,
    llm: BaseChatModel,
    *,
    org_id: str = DEFAULT_ORG_ID,
    row_limit: int = DEFAULT_ROW_LIMIT,
):
    """Compile the list_tables -> get_schema -> generate_query -> check_query ->
    run_query -> synthesize_answer graph, with the error-loop back to
    generate_query on either a check_query or run_query failure, capped at
    MAX_SQL_RETRIES (plan §4 hardening requirement #4).
    """
    nodes = SQLAgentNodes(db, llm, org_id=org_id, row_limit=row_limit)

    graph = StateGraph(SQLAgentState)
    graph.add_node("list_tables", nodes.list_tables)
    graph.add_node("get_schema", nodes.get_schema)
    graph.add_node("generate_query", nodes.generate_query)
    graph.add_node("check_query", nodes.check_query)
    graph.add_node("run_query", nodes.run_query)
    graph.add_node("synthesize_answer", nodes.synthesize_answer)
    graph.add_node("fail_closed", nodes.fail_closed)
    graph.add_node("decline", nodes.decline)

    graph.set_entry_point("list_tables")
    graph.add_edge("list_tables", "get_schema")
    graph.add_edge("get_schema", "generate_query")

    # An out-of-scope generation (the model emitted an OUT_OF_SCOPE line rather
    # than SQL) routes straight to the decline terminal node -- bypassing the
    # SELECT guard, execution, and answer synthesis entirely. Everything else
    # proceeds to check_query as before.
    graph.add_conditional_edges(
        "generate_query",
        SQLAgentNodes.route_after_generate,
        {"check": "check_query", "decline": "decline"},
    )

    graph.add_conditional_edges(
        "check_query",
        SQLAgentNodes.route_after_check,
        {"proceed": "run_query", "retry": "generate_query", "fail_closed": "fail_closed"},
    )
    graph.add_conditional_edges(
        "run_query",
        SQLAgentNodes.route_after_run,
        {"proceed": "synthesize_answer", "retry": "generate_query", "fail_closed": "fail_closed"},
    )

    graph.add_edge("synthesize_answer", END)
    graph.add_edge("fail_closed", END)
    graph.add_edge("decline", END)

    return graph.compile()


def _initial_state(question: str, org_id: str) -> SQLAgentState:
    return SQLAgentState(
        messages=[],
        question=question,
        org_id=org_id,
        table_names=[],
        schema_context="",
        generated_sql="",
        sql_retry_count=0,
        query_error_log=[],
        result_rows=None,
        final_answer="",
        success=False,
        done=False,
        last_step_ok=False,
        out_of_scope=False,
        out_of_scope_reason="",
    )


def run_sql_agent(
    question: str,
    thread_context: dict[str, Any] | None = None,
    *,
    llm: BaseChatModel | None = None,
    db: SQLDatabase | None = None,
    database_url: str | None = None,
    row_limit: int = DEFAULT_ROW_LIMIT,
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
) -> SQLAgentResult:
    """Answer one natural-language question against the live Postgres schema.

    This is the black-box entry point another node/service calls -- it owns
    building/caching the SQLDatabase + LLM if not supplied, running the
    compiled subgraph to completion, and shaping the result into
    SQLAgentResult. thread_context is accepted (persona/scope/org_id) for
    forward compatibility with the parent reason subgraph's state, but the
    SQL agent itself stays schema/contract-independent per plan §4/§11 -- it
    only reads org_id out of it if present.
    """
    thread_context = thread_context or {}
    org_id = thread_context.get("org_id", DEFAULT_ORG_ID)

    resolved_llm = llm or _default_llm()
    resolved_db = db or _build_database(
        database_url or os.environ["DATABASE_URL"], statement_timeout_ms
    )

    compiled = build_sql_agent_subgraph(resolved_db, resolved_llm, org_id=org_id, row_limit=row_limit)
    final_state = compiled.invoke(_initial_state(question, org_id))

    result_rows = final_state.get("result_rows")
    return SQLAgentResult(
        question=question,
        answer=final_state.get("final_answer", ""),
        generated_sql=final_state.get("generated_sql") or None,
        success=bool(final_state.get("success")),
        sql_retry_count=final_state.get("sql_retry_count", 0),
        query_error_log=final_state.get("query_error_log", []),
        rows_returned=len(result_rows) if result_rows is not None else None,
    )
