# MoveInSync-AIR — Working Backlog

> Living list of proposed work, decisions, and research from the design sessions.
> **Append-only in spirit** — add new items at the bottom of each section; don't delete, strike through (`~~done~~`) or mark `[x]` when complete.
> Status legend: `[ ]` proposed · `[~]` designed/approved, not built · `[x]` done · `[!]` blocked/needs decision

_Last updated: 2026-09-05_

---

## 0. Program shape (agreed)

Large effort decomposed into four sub-projects. **Sequence: A → B → C (→ D).**

- **SP-A — Live spine + demo "time-machine"** `[~]` **← chosen first**
  Connect frontend to the built `/ws/{persona}` socket; live ticking event feed; freshness heartbeat; autonomy badge (act-vs-ask); "Simulate live day" control (drives `replay.py`); timeframe/scrubber; flip off mock data. Rationale: pure visibility fix, backend already built & verified, lowest collision risk with concurrent SP-C frontend work.
- **SP-B — Cadence & autonomy policy** `[ ]`
  Per-signal detection-latency budget + confidence×severity → action matrix + noise reduction (dedup/hysteresis/adaptive thresholds). Feeds SP-A's autonomy badge with real policy instead of a simple threshold. Invisible without SP-A to show it through.
- **SP-C — Insight layers + role depth** `[ ]` (concurrent session's territory)
  signal→insight→recommendation→action tiering; shared severity color system; drill-down to rows; surface the unused `feedback` table; new per-role panels.
- **SP-D — Exec cross-BU view** `[ ]`
  5-org scorecard + billing-recovery ledger + EV-transition ROI.

---

## 1. SP-A component checklist (design not yet approved — DO NOT BUILD until sign-off)

- [x] WS client hook (`frontend/src/api/liveEvents.ts`) → subscribes `/api/ws/{persona}`, backoff reconnect. **Verified live.**
- [x] Live event feed panel (`LiveEventFeed.tsx`) — newest-first ticking list. **Verified: delay_breach event streamed in live.**
- [x] Freshness heartbeat — "LIVE · updated Ns ago". **Verified.**
- [x] Autonomy badge — "Auto-resolved by agent" (green) / amber "Needs your approval". **Verified (Critical + Auto-resolved rendered).**
- [x] "Simulate live day" button + `POST /api/demo/replay` (backend `app/api/demo.py`) — injects real trips via `replay.py` then runs `run_pipeline` inline. **Verified end-to-end.** (Decided: in-app button + endpoint.)
- [x] Scrubber/timeframe — All / Last 15m / Last 1h filter over retained feed history. (Scoped simple, per decision.)
- [x] Flip off mock data — `VITE_USE_MOCK=false`; **verified real vanta-Aus data renders** (escort cards, 443-min delay).
- [x] Additive only — new components + hook + `demo.py`; no dashboard/chart/color-system edits.
- [x] FOLLOW-UP DONE: `replay.py` now also replays the `commute` leg (so the escort detector's trip→commute→employee join resolves) AND, for `escort_violation`, preserves the original late-night time-of-day (a `now - anchor` shift moved trips out of the 21:00-06:00 window). **Verified: all four scenarios now fire their target signal (delay_breach, escort_compliance_violation, billing_discrepancy, emissions_over_target).**

### SP-A open questions to resolve at design time
- Which persona/org to wire first for the demo? (Data suggests **vanta-Sea** for the safety story — most incidents + ~28K real escort violations; **pinnacle-Slc** for volume/feedback.)
- "Simulate live day" trigger surface (in-app vs CLI).
- Scrubber semantics.
- Honesty constraint: **no GPS/geo data** → live status board, NOT a moving-dot map.

---

## 2. Chat / Q&A hardening (designs verified, approved to build? NO — waiting)

- [x] **T1 — Chat side-effect-free.** `skip_act` on `TopState` + conditional edge in `graph.py`; `run_chat_turn` sets it. **Verified: golden run confirmed chat wrote 0 notification rows (scope='chat' 5→5), trace preserved.**
- [x] **T2 — Q&A scope guardrail (contract-driven).** `{scope_context}` built from `get_contract()` (+ per-entity `description:` added to contract); `OUT_OF_SCOPE:` sentinel → `decline` node. **Verified: 12 in-scope domains answered with real numbers; "weather"/"CEO of Google" declined without fabricated figures.**
- [x] **T3 — Golden Q&A regression set.** `backend/tests/golden_qa.py`, contract-derived domains, structural assertions. **Verified: 14/14 pass in HTTP mode.**
- [x] **T4 — No-hardcode cleanup.** `act/nodes.py` fallbacks now use `get_contract().default_org_id`. (Verified via import; covered by rebuilt image.)

---

## 3. Value-add roadmap (from tech-debt review — not yet scheduled)

- [x] **V1 (reimagined — local, no external integration). DONE & verified.** In-UI "Communications & Reports Outbox" (`frontend/src/pages/OutboxPage.tsx`): Reports (Preview/Copy link) + Communications (Copy / Download .md / Mark as sent → "Sent ✓ (simulated)" with "in production this emails the vendor's dispatch desk"). Reuses existing `/api/reports` + `/api/notifications`; simulated send persisted in localStorage; no backend changes. Original external-channel form: surface the agent's drafted vendor/leadership **communication text** (from `communication_drafter`) + generated HTML **reports** in one "what the agent would send" view. Per artifact: full preview, status badge (`Draft ready → Approved → Sent ✓ (simulated)`), and local actions Copy / Download (.md/.html) / "Mark as sent" (labeled "in production this emails the vendor dispatch desk"). Proves the closed-loop report/communicate capability locally. **Queued to build after SP-A + chat/QA land** (avoids frontend + `act/nodes.py` collision). May need a small backend read to expose the drafted communication text if not already persisted — verify at build time.
- [ ] Wire the built-but-dormant **LangMem memory** into live reason/act prompts (recall exists, never injected).
- [ ] Predictive signal (forecast delay/cost breach) — flips reactive → proactive; pairs with external enrichment.
- [ ] External enrichment (weather/traffic) — research agent is a curated lookup, not web/API search.
- [ ] Fix README + real architecture diagram (current docs are the hackathon template, undersell the real system).
- [x] Latent bug RESOLVED: the synthetic `triggers.sql` (NOTIFY on `public.route_trips` etc.) was removed entirely as part of the synthetic-schema teardown — the event path now relies solely on `real_data/triggers.sql` on `mis.*`. See §7.
- [ ] Config-drive sense thresholds per-org (currently hardcoded constants).
- [ ] Multi-tenancy seam (loop N orgs, scoped principal), per-tenant token-based LLM budget + response cache (auth/CORS are demo-open today).

---

## 4. Reference (grounding for designs)

- **Real orgs (mis.trip, May–Jul 2026):** pinnacle-Slc (251,774 trips), vanta-Sea (180,064; 20,105 incidents; ~27,961 unescorted late-night female trips), vanta-Aus (70,199), catalyst-Sac (65,214), orbit-Slc (41,542).
- **Live loop (all backend built):** `replay.py` → NOTIFY → scheduler → sense→reason→act → Redis publish `notifications:{persona}` → WS `/api/ws/{persona}` → frontend (not yet connected). Frontend defaults to mock (`VITE_USE_MOCK`).
- **Cadence framework:** detection-latency budget (sample ≤ ½ time-to-irreversible-harm; match to human reaction speed). Safety=event-push/seconds, delay=1–5 min, cost=hourly/daily, carbon=weekly. Autonomy gate = confidence×severity → auto-act (reversible+high-conf) / notify / escalate / suppress. Healthy alerting = 30–50% actionable (vs healthcare alarms 72–99% false).
- **Color/explainability:** RAG + ISO 22324 semantics, IBM Carbon tokens (critical `#da1e28`, warning `#f1c21b` dark-text, serious `#ff832b`, info `#0043ce`, success `#24a148`), never hue-alone (icon+label, WCAG 3:1). Shneiderman overview→zoom→detail; PAIR/HAX for agent transparency + HITL approval states.

---

## 6. Scale + UX pass — DONE & verified (2026-09-05)

- [x] **Pagination + indexing (Notifications + Agent Activity).** `GET /notifications` & `/activity` now take `limit`/`offset` and return `{items, total}`; frontend "Load more". Migration `002_pagination_indexes.sql` adds composite `(org_id, persona, created_at DESC)` on `agent_notifications` and `(org_id, persona, generated_at DESC)` on `agent_reports` (applied + verified). **Verified: Load more 51→ "1 left" (notifications), 717 total (activity).**
- [x] **SSE.** `GET /api/sse/{persona}` (StreamingResponse, reuses the Redis `notifications:{persona}` + new `activity:{org}` channels). Frontend `liveStream.ts` EventSource wires into NotificationInbox + AgentActivity. `activity_log` now publishes activity frames; `demo.py` records the pipeline run so Simulate lights both. **Verified end-to-end: one replay delivered a `notification` AND an `activity` frame over SSE.**
- [x] **"Generate report" button.** `POST /api/reports/generate` wraps `run_report`; button in ReportsSection. **Verified: created report id=4 in ~10s, appears in list.**
- [x] **Live-page data window.** `GET /api/data-coverage` (MIN/MAX trip_date + count); pill on Live header. **Verified: "Data window: 2026-05-01 → 2026-09-05 (70,216 trips)".**
- [x] **Mock-default flip.** `api/index.ts` now defaults to REAL; mock is opt-in (`VITE_USE_MOCK === "true"`).
- [x] **Bugfix: Outbox "couldn't load".** It requested `limit:500` but the paginated endpoint caps at 200 (422). Changed to `limit:200`. **Verified loads.**
- Audit answer (no gaps): every real ApiClient method → a registered backend route backed by real DB queries; only intentional statics remain (`getRoles`), mock client is the standalone-dev fallback only. "Do all features need live?" → No: live/SSE on Notifications + Activity (time-sensitive); dashboards/reports/trends stay fetch-on-load.

## 7. Synthetic schema removal — DONE & verified (2026-09-05)

Context: a "99.9% of data is missing" report was querying the synthetic `public` seed schema; the real data is fully in `mis.*`. Decision: run on real data only, remove synthetic entirely.

- [x] Dropped the 12 synthetic `public` business tables (route_trips, routes, vendors, drivers, teams, employees, route_costs, safety_incidents, vendor_invoices, emissions_log, commute_logs, attendance_records) + the dead unused `sql_agent_examples`. Kept infra/app/reference: agent_notifications, agent_reports, chat_threads, pipeline_runs, data_quality_flags, sustainability_targets, LangGraph checkpoints, LangMem store. **Verified: mis.trip still 608,832; all endpoints return data; no errors.**
- [x] `schema.sql` reduced to the kept tables; `backend/db/triggers.sql` deleted (synthetic-only NOTIFY — resolves the V6 latent bug); `seed/generate.py` reduced to a reference-only seeder (sustainability_targets only, matching the 3 metric_names the reason layer looks up); `seed/entrypoint.sh` gates retargeted off `teams` → `sustainability_targets` and the triggers.sql apply removed.
- [x] Deleted `data_contract.synthetic.yaml`; updated the now-stale references (data_contract.yaml header, docker-compose.yml, db/README.md, real_data/README.md, api_schema.sql, DEMO_RUNBOOK.md, listener.py/Dockerfile comments).
- Full fresh-boot acceptance (`docker compose down -v && up --build`) **RUN & GREEN**. It surfaced + fixed two latent fresh-boot bugs the old path never hit:
  1. `seed/entrypoint.sh` real-ingest gate `SELECT ... EXISTS(SELECT 1 FROM mis.trip) ...` parse-failed when `mis.trip` didn't exist yet (Postgres resolves tables at parse time) → aborted seed under `set -e`. Fixed: split into a `to_regclass` existence check then the count.
  2. `real_data/triggers.sql` used `notify_moveinsync_event()` but that function was defined only in the deleted synthetic `triggers.sql` → "function does not exist". Fixed: `real_data/triggers.sql` now defines the function itself (self-contained).
- Post-fresh-boot validation: contract↔DB check = **SCHEMA VALID (17/17 entities, all columns)**; `public` has only kept infra/reference (+ runtime LangGraph checkpoints); `mis.trip` = 608,793; sustainability_targets = 3 metrics (per-org); mis.* NOTIFY triggers + function present; all API endpoints OK.

## 5. Session decisions log

- 2026-09-05: Program decomposed A/B/C/D; sequence A→B→C. **SP-A chosen first.** Build paused by user ("wait to build"). Chat/Q&A designs (T1–T3) verified, not yet approved to build. No concurrent frontend changes visible in tree at decision time.
- 2026-09-05: User approved build ("build all") scoped to SP-A + T1–T4 (V-series held; V1 reimagined local). Built via 2 parallel agents. **SP-A + T1–T4 done & verified** (golden 14/14, live feed streamed a real delay_breach event). V1 (Outbox) building.
- 2026-09-05 (infra): Docker VM disk hit 100% full ("no space left on device"); host `/System/Volumes/Data` was at 1.3Gi free with a 330GB `Docker.raw`. Fixed by killing wedged builds, `docker builder prune -af` (reclaimed 40GB) + dangling image prune, and a Docker Desktop/system restart which triggered compaction → **304Gi free restored**. Volumes (postgres data) never pruned. Note for later: 243GB still sits in Docker volumes (old DB copies worth reviewing). Also: host `~/.docker/config.json` has an expired `docker-credential-gcloud` helper for `*-docker.pkg.dev` that spams build errors — pre-existing, worth removing.
