"""SELECT-only guard + LIMIT enforcement, driven by sqlglot's parsed AST.

Deliberately AST-based rather than string/regex matching (e.g. checking for
"DROP" as a substring) -- naive string checks are trivially defeated by
comments, whitespace tricks, or the word appearing inside a string literal.
Parsing with sqlglot and inspecting the statement's node type is the only
reliable way to know "is this actually a single SELECT".
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

SQL_DIALECT = "postgres"


class SQLSyntaxError(Exception):
    """Raised when sqlglot cannot parse the generated SQL at all."""


class SQLSecurityError(Exception):
    """Raised when the parsed SQL is not a single, standalone SELECT statement."""


@dataclass
class CheckedQuery:
    original_sql: str
    safe_sql: str
    had_limit_injected: bool


def parse_or_raise(sql: str) -> list[exp.Expression]:
    """Local, no-DB-round-trip syntax pre-flight (hardening requirement #2)."""
    cleaned = sql.strip()
    if not cleaned:
        raise SQLSyntaxError("generated SQL is empty")
    try:
        statements = sqlglot.parse(cleaned, read=SQL_DIALECT)
    except Exception as exc:  # sqlglot raises its own ParseError subclasses
        raise SQLSyntaxError(f"sqlglot could not parse the query: {exc}") from exc

    statements = [s for s in statements if s is not None]
    if not statements:
        raise SQLSyntaxError("query parsed to zero statements")
    return statements


def enforce_select_only(sql: str, *, row_limit: int) -> CheckedQuery:
    """Reject anything that isn't exactly one SELECT; inject a LIMIT if missing.

    This is the hard security boundary (hardening requirement #5): even if the
    LLM is jailbroken or hallucinates a mutating statement, this function is
    what actually stands between generated text and the database.
    """
    statements = parse_or_raise(sql)

    if len(statements) > 1:
        raise SQLSecurityError(
            f"query contains {len(statements)} statements (stacked via ';'); "
            "only a single statement is allowed"
        )

    statement = statements[0]
    if not isinstance(statement, exp.Select):
        raise SQLSecurityError(
            f"only SELECT statements are allowed, got {type(statement).__name__}: "
            f"{statement.sql(dialect=SQL_DIALECT)[:120]}"
        )

    # Belt-and-suspenders: walk the whole tree for any DML/DDL node a
    # SELECT could smuggle in (e.g. via a CTE writing through a data-modifying
    # WITH clause in dialects that allow it, or a nested Command).
    forbidden_types = (
        exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter,
        exp.Create, exp.TruncateTable, exp.Grant, exp.Command,
    )
    for node in statement.walk():
        node_expr = node[0] if isinstance(node, tuple) else node
        if isinstance(node_expr, forbidden_types):
            raise SQLSecurityError(
                f"query contains a disallowed {type(node_expr).__name__} node"
            )

    had_limit_injected = False
    existing_limit = statement.args.get("limit")
    if existing_limit is None:
        statement = statement.limit(row_limit)
        had_limit_injected = True
    else:
        try:
            limit_value = int(existing_limit.expression.this)
            if limit_value > row_limit:
                statement.set("limit", exp.Limit(expression=exp.Literal.number(row_limit)))
                had_limit_injected = True
        except (AttributeError, ValueError, TypeError):
            statement = statement.limit(row_limit)
            had_limit_injected = True

    safe_sql = statement.sql(dialect=SQL_DIALECT)
    return CheckedQuery(original_sql=sql, safe_sql=safe_sql, had_limit_injected=had_limit_injected)
