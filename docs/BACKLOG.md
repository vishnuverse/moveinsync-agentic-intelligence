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
- FOLLOW-UP (small): `replay.py` copies trip+cost+incident+emission but NOT the linked `commute`/`employee` rows, so the `escort_violation` scenario doesn't fire its detector (only `delay_spike` fully lights up). Extend `_fetch_trip_cluster` to also replay commute/employee for the escort demo.

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
- [ ] Latent bug: `triggers.sql`/`sense/listener.py` reference synthetic tables while active contract is `mis.*` — real-data NOTIFY relies on `real_data/triggers.sql`; add golden test + fix.
- [ ] Config-drive sense thresholds per-org (currently hardcoded constants).
- [ ] Multi-tenancy seam (loop N orgs, scoped principal), per-tenant token-based LLM budget + response cache (auth/CORS are demo-open today).

---

## 4. Reference (grounding for designs)

- **Real orgs (mis.trip, May–Jul 2026):** pinnacle-Slc (251,774 trips), vanta-Sea (180,064; 20,105 incidents; ~27,961 unescorted late-night female trips), vanta-Aus (70,199), catalyst-Sac (65,214), orbit-Slc (41,542).
- **Live loop (all backend built):** `replay.py` → NOTIFY → scheduler → sense→reason→act → Redis publish `notifications:{persona}` → WS `/api/ws/{persona}` → frontend (not yet connected). Frontend defaults to mock (`VITE_USE_MOCK`).
- **Cadence framework:** detection-latency budget (sample ≤ ½ time-to-irreversible-harm; match to human reaction speed). Safety=event-push/seconds, delay=1–5 min, cost=hourly/daily, carbon=weekly. Autonomy gate = confidence×severity → auto-act (reversible+high-conf) / notify / escalate / suppress. Healthy alerting = 30–50% actionable (vs healthcare alarms 72–99% false).
- **Color/explainability:** RAG + ISO 22324 semantics, IBM Carbon tokens (critical `#da1e28`, warning `#f1c21b` dark-text, serious `#ff832b`, info `#0043ce`, success `#24a148`), never hue-alone (icon+label, WCAG 3:1). Shneiderman overview→zoom→detail; PAIR/HAX for agent transparency + HITL approval states.

---

## 5. Session decisions log

- 2026-09-05: Program decomposed A/B/C/D; sequence A→B→C. **SP-A chosen first.** Build paused by user ("wait to build"). Chat/Q&A designs (T1–T3) verified, not yet approved to build. No concurrent frontend changes visible in tree at decision time.
- 2026-09-05: User approved build ("build all") scoped to SP-A + T1–T4 (V-series held; V1 reimagined local). Built via 2 parallel agents. **SP-A + T1–T4 done & verified** (golden 14/14, live feed streamed a real delay_breach event). V1 (Outbox) building.
- 2026-09-05 (infra): Docker VM disk hit 100% full ("no space left on device"); host `/System/Volumes/Data` was at 1.3Gi free with a 330GB `Docker.raw`. Fixed by killing wedged builds, `docker builder prune -af` (reclaimed 40GB) + dangling image prune, and a Docker Desktop/system restart which triggered compaction → **304Gi free restored**. Volumes (postgres data) never pruned. Note for later: 243GB still sits in Docker volumes (old DB copies worth reviewing). Also: host `~/.docker/config.json` has an expired `docker-credential-gcloud` helper for `*-docker.pkg.dev` that spams build errors — pre-existing, worth removing.
