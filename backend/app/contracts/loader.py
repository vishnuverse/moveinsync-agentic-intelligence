"""Typed accessor for backend/config/data_contract.yaml (plan §3).

Every node/query that needs a physical table or column name resolves it
through here instead of embedding a literal string. If the schema changes
(e.g. real MoveInSync data lands), only data_contract.yaml needs to change --
this module and its callers do not.

Usage:
    from app.contracts import get_contract

    contract = get_contract()
    trip = contract.entity("trip")
    trip.table                        # "route_trips"
    trip.column("scheduled_time")     # "scheduled_arrival"
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import yaml

_DEFAULT_CONTRACT_PATH = Path(__file__).resolve().parents[2] / "config" / "data_contract.yaml"
_CONTRACT_PATH_ENV_VAR = "DATA_CONTRACT_PATH"


class ContractError(Exception):
    """Raised when code asks the contract for an entity/column it doesn't define.

    Fails loud on purpose: a silent fallback here would defeat the point of
    having a single source of truth for table/column names.
    """


@dataclass(frozen=True)
class EntityContract:
    name: str
    table: str
    _columns: Mapping[str, str] = field(repr=False)
    # Optional human-readable business description (data_contract.yaml's
    # `description:` per entity). Used to phrase the Q&A scope boundary in
    # business terms (see the SQL agent's scope_context); falls back to the
    # entity name when absent, so it is purely additive.
    description: str | None = None

    def column(self, logical_name: str) -> str:
        try:
            return self._columns[logical_name]
        except KeyError as exc:
            known = ", ".join(sorted(self._columns)) or "(none)"
            raise ContractError(
                f"entity '{self.name}' has no column '{logical_name}' in the data contract. "
                f"Known columns for '{self.name}': {known}"
            ) from exc

    def has_column(self, logical_name: str) -> bool:
        return logical_name in self._columns

    @property
    def columns(self) -> Mapping[str, str]:
        return dict(self._columns)


@dataclass(frozen=True)
class Contract:
    version: int
    default_org_id: str
    _entities: Mapping[str, EntityContract] = field(repr=False)

    def entity(self, logical_name: str) -> EntityContract:
        try:
            return self._entities[logical_name]
        except KeyError as exc:
            known = ", ".join(sorted(self._entities)) or "(none)"
            raise ContractError(
                f"no entity '{logical_name}' in the data contract. Known entities: {known}"
            ) from exc

    def has_entity(self, logical_name: str) -> bool:
        return logical_name in self._entities

    @property
    def entity_names(self) -> list[str]:
        return sorted(self._entities)


def _parse(raw: dict) -> Contract:
    if "entities" not in raw or not isinstance(raw["entities"], dict):
        raise ContractError("data contract is missing a top-level 'entities' mapping")

    entities: dict[str, EntityContract] = {}
    for entity_name, entity_def in raw["entities"].items():
        if "table" not in entity_def:
            raise ContractError(f"entity '{entity_name}' is missing a 'table' key")
        columns = entity_def.get("columns") or {}
        if not isinstance(columns, dict):
            raise ContractError(f"entity '{entity_name}' has a non-mapping 'columns' value")
        description = entity_def.get("description")
        entities[entity_name] = EntityContract(
            name=entity_name,
            table=entity_def["table"],
            _columns=dict(columns),
            description=str(description) if description is not None else None,
        )

    return Contract(
        version=raw.get("version", 1),
        default_org_id=raw.get("default_org_id", "moveinsync-demo"),
        _entities=entities,
    )


def load_contract(path: str | Path | None = None) -> Contract:
    """Parse a data_contract.yaml from disk. Not cached -- use get_contract() for the cached singleton."""
    resolved = Path(path) if path is not None else Path(os.environ.get(_CONTRACT_PATH_ENV_VAR, _DEFAULT_CONTRACT_PATH))

    if not resolved.exists():
        raise ContractError(f"data contract file not found at {resolved}")

    with resolved.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict):
        raise ContractError(f"data contract file at {resolved} did not parse to a mapping")

    return _parse(raw)


@functools.lru_cache(maxsize=1)
def get_contract() -> Contract:
    """Cached singleton -- the contract is loaded once per process and reused everywhere."""
    return load_contract()
