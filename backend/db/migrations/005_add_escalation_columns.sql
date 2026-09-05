-- SP-B escalation hierarchy (plan §A1). Adds the escalation-tracking columns
-- to agent_notifications on an ALREADY-SEEDED volume -- a fresh db built
-- from db/schema.sql already has these. Additive-only (IF NOT EXISTS), safe
-- to re-run. Applied by the orchestrator against the running DB; not applied
-- by db/seed/entrypoint.sh (same rationale as 003/004).
--
-- `escalated_at IS NULL` means "not yet escalated" (or not eligible -- e.g.
-- already resolved). The escalation-hierarchy check (folded into the same
-- scheduler tick as app.graph.supervisor.run_pipeline, see
-- app.graph.supervisor.ESCALATION_CHAIN) scans exactly this predicate, so
-- once a row is escalated it is stamped here and never scanned/escalated a
-- second time. `escalated_to_persona` records which persona the escalation
-- notification went to, purely for display (Trace Drawer/Outbox can show
-- "escalated to Transport Head after 4 hours unacknowledged").

ALTER TABLE agent_notifications ADD COLUMN IF NOT EXISTS escalated_at TIMESTAMPTZ;
ALTER TABLE agent_notifications ADD COLUMN IF NOT EXISTS escalated_to_persona TEXT;

CREATE INDEX IF NOT EXISTS idx_agent_notifications_escalation_scan
    ON agent_notifications (org_id, status, severity, created_at) WHERE escalated_at IS NULL;
