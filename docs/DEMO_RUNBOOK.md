# Jury Demo Runbook

One section per judging criterion (official problem statement PDF, §9). Each names the exact live action and what it proves. Rehearse this start to finish before the actual session — the interrupt/resume flow especially, since it's the easiest moment to fumble live.

**Setup (before the jury arrives):**
```bash
docker compose up -d
docker compose logs cloudflared   # grab the public URL
```
Confirm `docker compose ps` shows all 7 services healthy and the `seed` job completed (`docker compose logs seed | tail -30` — should end with "seed: done", and if `data/` is present, real-data ingestion + triggers applied).

---

## 1. Functionality (25 pts) — "it runs, end to end, on the provided dataset"

- Open the app (local or the Cloudflare URL). Dashboards load immediately with real data already visible — no empty states, no "click to load."
- Switch personas via the role switcher. Point out: same backend, same data, just re-scoped — not three separate apps.

## 2. Business impact & experience (35 pts) — "does it land, is it leadership-ready"

- **Transport Head:** open the billing discrepancy view — real ₹ recovery number, not a placeholder. Open a generated leadership report; scroll it as a plain HTML file, no editing — this is the bonus requirement ("forward to leadership untouched") made concrete.
- **Transport Manager:** open the escort-compliance card — a real safety metric with a real compliance %, not a toy number.
- Every metric card shows its `context_note` — point at one and read it aloud: this is the mandatory "contextualise against a reference point" requirement, visibly satisfied on every single card, not just claimed in the README.

## 3. Agentic design & cost at scale (20 pts) — "is AI solving a real problem, can it actually run"

**This is the centerpiece — the live streaming replay.**

```bash
DATABASE_URL=$DATABASE_URL python backend/db/real_data/replay.py --scenario delay_spike --count 5 --interval-seconds 3
```

Say explicitly: *"These are real trips from the provided dataset, re-inserted with today's timestamp — not synthetic, not scripted fake events. Watch the Agent Activity feed."* Within a few seconds of each insert, a new entry appears with nobody clicking anything — the `LISTEN/NOTIFY` event fires, sense detects, reason analyzes, act notifies, and the UI updates over the WebSocket push in real time.

- Open the Trace Drawer on the new alert. Show the generated SQL, the retry count (if the SQL agent self-corrected), and the sense→reason→act step list — real reasoning, not a canned response.
- Point at the visible generated SQL as the "grounded, not hallucinated" proof, and mention the Redis-backed LLM circuit breaker as the cost-at-scale story: every call is metered, capped, and fails fast rather than looping.
- Optionally repeat with `--scenario escort_violation` or `--scenario emissions_over_target` to show a second detector reacting live.

## 4. Architecture & code quality (20 pts) — "sound structure, deployable, a team could build on it"

- **Decoupling proof:** show `backend/config/data_contract.yaml` — every sense/reason/act query and dashboard metric goes through these logical→physical mappings. Say: *"Point this at MoveInSync's real production schema by editing this one file — nothing else in the codebase changes."*
- **Interrupt/HITL walkthrough:** trigger (or already-triggered from the replay above) a `needs-intervention` item — e.g. the escort-violation flow. Open it, show the trace (why the agent wants to act), click Approve. Point out: the send only fires after approval, and re-clicking Approve doesn't double-send — LangGraph's `interrupt()`/`Command(resume=...)` primitive, not a hand-rolled flag.
- Brief repo walkthrough if time allows: `backend/app/graph/{sense,reason,act}/` mirrors the sense→reason→act architecture directly in the folder structure.

---

## Fallback if live replay misbehaves

If the network/LLM call is slow or flaky live, the dashboards and reports already contain real data from the initial seed — fall back to the "Business impact" walkthrough (section 2) and the pre-generated Trace Drawer examples rather than waiting on a live call in front of the jury. Mention the replay is optional flourish; the system already ran autonomously via the scheduler since boot.
