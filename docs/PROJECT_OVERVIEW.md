# MoveInSync-AIR — Project Overview

## The problem (official brief)

Large enterprises move a few hundred to a few thousand employees daily
through a mix of home/nodal pick-and-drop cabs and fixed-route shuttles.
Transport managers are accountable for cost, safety, experience, and
sustainability — but most of their time goes into assembling data, not
acting on it. A metric without context is just a number: "OTA is 78%"
matters far less than "it was 85% last month, SLA is 90%, and two vendors
are responsible for the gap." That benchmarking is currently absent. See
[`PROBLEM_STATEMENT.md`](PROBLEM_STATEMENT.md) for the full official brief.

## What we built

An agentic layer that senses operational events in the real (anonymised)
MoveInSync dataset, reasons about their business impact against a reference
point, and acts — autonomously where the action is reversible and
high-confidence, with a human-approval gate (LangGraph `interrupt()`) where
it isn't.

It serves all three named personas from one shared backend and dataset,
re-scoped by role rather than built as three separate apps:

- **Transport Manager (operational):** live escort-compliance and safety
  monitoring, delay/incident alerts, same-day operational view.
- **Line Manager (team-level):** commute-attendance correlation, isolating
  transport-caused delay from genuine no-shows for their team.
- **Transport & Facilities Head (strategic):** billing-slab discrepancy
  auditing with a real ₹ recovery number, sustainability/EV-transition
  tracking, leadership-ready generated reports.

A conversational agent (NL Q&A, grounded in generated SQL shown alongside the
answer) and a live event feed (sense→reason→act reacting to real data with
no polling) sit across all three.

For the full feature-by-feature blueprint — sense/reason/act logic, exact
data fields, and measurement protocols per feature — see
[`moveinsync-prd-v3.md`](moveinsync-prd-v3.md). For the real architecture
and how these pieces connect, see [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Where we stand against the official requirements

All four mandatory requirements are met, all three good-to-have items are
met, and the deployability/leadership-output bonus items are addressed. This
is tracked, kept current, and re-verified (not just asserted once) in
[`BACKLOG.md`](BACKLOG.md) — that document, not this one, is the place to
check what's actually built today versus still open.

## Live demo

**https://app.inferencezero.com** — the running system, on the same
anonymised sample dataset used locally.
