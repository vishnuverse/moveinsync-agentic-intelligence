# Demo Prep: Live Streaming Replay + UI Polish

**Status:** Draft — isolated from the main build plan (`/Users/vishnuanilkumar/.claude/plans/extend-it-using-2-glistening-hammock.md`) so the two don't collide; this covers post-build demo/presentation work, not core architecture.

**Context:** The full agentic backend (sense/reason/act, SQL agent, LangMem, HITL interrupts, FastAPI, docker-compose) and the real MoveInSync dataset (~2.5M rows, `mis` schema) are built and committed. Two things remain before the jury demo: (1) a way to *show* the system's live autonomy convincingly using real data, and (2) the frontend UI, which is functional but was built for correctness/contract-matching, not visual polish — not yet held to the standard the backend work deserves. Forecasting/TabFM work is explicitly out of scope per direction — dropped, not deferred.

---

## Part 1: Live Streaming Replay Demo

### Gap found (verified read-only against the running `moveinsync-pg-verify` container)
`backend/db/triggers.sql`'s `NOTIFY` triggers only exist on the old synthetic `public.route_trips|route_costs|safety_incidents|emissions_log`. The real-data ingestion switched the data contract to `mis.*` but never added equivalent triggers there — so today, a live insert into `mis.trip` etc. would only surface via the interval poller (up to several minutes' lag), not instantly via the event-driven path. This is the one concrete fix needed before "live" streaming actually looks live.

**Fix:** `backend/db/real_data/triggers.sql` — reuses the existing `notify_moveinsync_event()` function (already schema-agnostic: built on `TG_TABLE_NAME`/`NEW.id`, reachable from any schema via the default search_path) with new triggers on `mis.trip` (`delay_detected`), `mis.incident` (`incident_detected`), `mis.cost` (`cost_detected`), `mis.emission` (`emission_detected`). Zero changes needed to `SenseEventListener` — same channel, same payload shape.

### Streaming replay tool
`backend/db/real_data/replay.py`: selects a batch of **already-ingested real rows** — including specific ones the F1 (escort compliance) and F2 (billing discrepancy) detectors are known to flag — and re-inserts them with fresh `id`s and `created_at`/timestamps set to "now", on a configurable timer (default: one trip + its linked cost/incident rows every 2s). Each insert fires the new trigger → sense detects → reason → act → notification/report, live, surfaced through the already-built Agent Activity feed + notification inbox + WebSocket push. No polling delay, no manual trigger click.

Framing for the jury: this isn't a simulation of something fake — it's the real dataset's actual values traveling through the exact same code path real MoveInSync traffic would use, just replayed at demo pace instead of the original multi-month pace. Say that explicitly.

Reuses real historical row *values* rather than holding back an unloaded "reserve" slice from ingestion — simpler, and avoids touching the already-committed, already-verified ingestion pipeline again.

CLI flags: `--interval-seconds`, `--count`, `--scenario` (`escort_violation` | `billing_discrepancy` | `delay_spike` | `emissions_over_target` — pre-selected real rows known to trigger each detector, so the presenter can reliably demo a specific feature on cue rather than hoping a random row trips the right one).

### Demo runbook
`docs/DEMO_RUNBOOK.md` — one section per judging criterion (official PDF §9), each naming the exact live action and what it proves:
- **Functionality (25 pts):** `docker compose up`, health check, dashboards already showing real data.
- **Business impact & experience (35 pts):** walk one persona's dashboard with real contextualized metrics (actual ₹ billing-discrepancy recovered, actual escort-compliance %), open a generated leadership report, show it's forward-ready untouched.
- **Agentic design & cost at scale (20 pts):** run the streaming replay live — insert real rows, watch the notification/activity feed react within seconds with nobody clicking anything; open the Trace Drawer to show the real generated SQL and the sense→reason→act chain; point at the Redis-backed LLM circuit breaker and the SQL agent's visible query as evidence this is real, cost-aware agentic behavior, not a wrapper.
- **Architecture & code quality (20 pts):** the decoupling proof (swap `data_contract.yaml` between synthetic/real, zero code changes), the interrupt/resume HITL flow (approve a needs-intervention item live, show no duplicate side effects), a brief repo-structure walkthrough.

---

## Part 2: UI Polish via `/impeccable`

### Current state (from `context.mjs`, run read-only)
No `PRODUCT.md`/`DESIGN.md` exist yet — this frontend was built for contract correctness (matches `frontend/src/api/types.ts` exactly, verified end-to-end against the live backend) but has never been through a design pass. Per the skill's own routing: a scoped refinement of existing code doesn't require the full `init`→`new-work` flow — `init` is offered, not required.

### Recommended approach
1. **Skip `init`'s full interview** given time constraints — brand direction is already fixed and non-negotiable (`docs/brandguidelines/brandguidelines.md`: Apple Green `#38AF48` primary, Malibu Blue `#8ED1FC` secondary/backgrounds-only, Outer Space `#32373C` text), so a from-scratch product-context interview adds ceremony without changing the actual constraint. Proceed directly on the existing code as context, per the skill's own "scoped fixes... do not need the new-surface flow" guidance.
2. **`critique` first** (UX heuristic scoring) on the full frontend — surfaces concrete, prioritized issues rather than guessing at what to fix blind.
3. **`polish`** (final quality pass) applying the critique's findings — this is the "Operate" mode per the skill's own mode taxonomy (dashboards/tools where task completion and scanability outrank marketing-style expression), which matches this app exactly: persona dashboards, a notification inbox, a trace drawer, a chat panel.
4. Run the skill's own mechanical detector once at the end (`detect.mjs`) per its instructions, fix what it finds in one batch, confirm with at most one more pass — not an open-ended polish loop.

### Scope boundaries
- Preserve all existing behavior, data contracts, and copy (this is refinement, not redesign) — the frontend already passed real end-to-end verification against the live backend; a visual pass must not change what any component fetches/renders logically.
- Stay inside `frontend/` — no backend changes.
- Brand colors are fixed inputs, not open questions for the critique/polish passes to reconsider.
- **Real logo assets, not a text wordmark.** `docs/brandguidelines/MoveInSync_MIS_black-01-01_3.svg` is the clean scalable vector mark (fill `#3DAE2B`, essentially the same green as the documented `#38AF48`) — use this as the primary in-app logo (header, favicon source) since it stays crisp at any size and is a tiny asset. `MoveInSync_idEggP6NUT_0.png`/`MoveInSync_idcbx839Jp_1.png` (icon+wordmark raster, two sizes) are available as fallback/social-preview assets if a raster format is needed anywhere the SVG doesn't fit (e.g. a favicon.ico for older browser support). Copy the chosen file(s) into `frontend/src/assets/` (or `public/`) rather than referencing `docs/` at runtime.

---

## Build Order
1. `backend/db/real_data/triggers.sql` (small, mechanical, mirrors existing pattern).
2. `backend/db/real_data/replay.py` + verify live against the running real-data Postgres (insert → NOTIFY → sense → reason → act → notification, timed).
3. `docs/DEMO_RUNBOOK.md`.
4. `/impeccable critique` on `frontend/`, then `/impeccable polish` applying its findings, then one `detect.mjs` pass + fixes.

## Verification
1. Run `replay.py --scenario delay_spike --count 5 --interval-seconds 2`; confirm each insert produces a live notification within a few seconds (no manual trigger, no multi-minute poll lag).
2. Confirm all four `--scenario` options produce their intended detector's signal.
3. Confirm `data_contract.yaml` can still swap to `data_contract.synthetic.yaml` with zero code changes (the decoupling proof, now doubly proven — once synthetically, once for real).
4. Walk `docs/DEMO_RUNBOOK.md` start to finish as a rehearsal before the actual jury session.
5. Frontend: confirm the app still builds and every existing route/interaction still works post-polish (no regressions from the visual pass).
