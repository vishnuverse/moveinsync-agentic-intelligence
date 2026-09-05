-- SP-B priority / notification-cadence tiers (plan §3). Adds the visibility-
-- delay column to agent_notifications on an ALREADY-SEEDED volume -- a fresh
-- db built from db/schema.sql already has this. Additive-only (IF NOT
-- EXISTS), safe to re-run. Applied by the orchestrator against the running
-- DB; not applied by db/seed/entrypoint.sh (same rationale as 003).
--
-- `scheduled_for IS NULL` means "immediate" -- every row written before this
-- migration (or by any code path that never sets it) keeps today's existing
-- behavior unchanged. A non-NULL value is a read-time visibility gate, not a
-- second copy of the notification: app/services/notifications_query.py's
-- list_notifications/count_notifications add `AND (scheduled_for IS NULL OR
-- scheduled_for <= now())` to their existing WHERE clause, and
-- app/graph/act/nodes.py's send_dispatch only pushes live (SSE/WS) when the
-- same condition holds at write time.

ALTER TABLE agent_notifications ADD COLUMN IF NOT EXISTS scheduled_for TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_agent_notifications_org_persona_scheduled
    ON agent_notifications (org_id, persona, scheduled_for);
