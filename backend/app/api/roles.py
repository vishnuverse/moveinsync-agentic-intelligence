"""GET /api/roles -- the 3 static personas. Not data-driven per the build
brief: this is a fixed product decision (plan §1's three personas), not
something that should vary by org/environment.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.schemas import Role

router = APIRouter(tags=["roles"])

ROLES: list[Role] = [
    Role(
        id="transport_manager",
        name="Transport Manager",
        description="Runs day-to-day fleet ops: routes, drivers, vendors, live SLA.",
    ),
    Role(
        id="line_manager",
        name="Line Manager",
        description="Owns team commute experience and attendance fairness.",
    ),
    Role(
        id="transport_head",
        name="Transport Head",
        description="Owns strategy: vendor contracts, cost, safety, sustainability.",
    ),
]


@router.get("/roles", response_model=list[Role])
def get_roles() -> list[Role]:
    return ROLES
