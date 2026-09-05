# Hackathon Submission — Team Sudo

MoveInSync **Agentic Intelligence**: an agent layer that senses, reasons and acts
over the real MoveInSync operations dataset.

## Contents

| File | What it is |
|---|---|
| `MoveInSync_TeamSudo_Deck.html` | The 22-slide presentation deck. Self-contained — open it in any browser, no server, no assets to unpack. |
| `screenshots/` | The product screenshots used in the deck, all captured from the running stack. |

## Viewing the deck

Open `MoveInSync_TeamSudo_Deck.html` in a browser.

- **← / →**, space, or PageUp/PageDown to move between slides; Home/End jump to the ends.
- **Print to PDF** (Cmd/Ctrl-P) exports all 22 slides at 16:9, one slide per page, for
  portals that require a file upload.

It is also published as a live page: <https://claude.ai/code/artifact/fd469e53-9e5d-40c8-ada4-d4e8d5aa9804>

## How the deck maps to the judging criteria

| Criterion | Points | Slides |
|---|---|---|
| Business impact | 35 | 3, 5–9 — three persona use cases, context on every metric, messy-data handling |
| Functionality | 25 | 19–22 — conversational agent, trace drawer, human-in-the-loop, live demo |
| Agentic design & cost at scale | 20 | 4, 13–18 — graph state, rules and thresholds, the filtering gate, LLM call map, budget guard |
| Architecture & code quality | 20 | 10–12, 18 — system architecture, the data contract, LangGraph architecture, SQL guardrails |

The system architecture (slide 10) and the LangGraph agentic architecture (slide 12) are
deliberately separate diagrams: one is the deployment view, the other is the graph topology.

## Notes on the numbers

Every figure in the deck was read off the running instance at the time of capture, not
estimated — row counts from `pg_stat_user_tables`, gate telemetry from
`public.gate_decisions`, data-quality counts from `public.data_quality_flags`. They move as
the scheduler keeps running, so treat them as a snapshot rather than fixed constants.
