-- Composite indexes backing the paginated, most-recent-first reads the API
-- serves per persona (GET /api/notifications, GET /api/reports). The existing
-- single-column idx_agent_notifications_persona / idx_agent_reports_persona
-- indexes can't satisfy the (org_id, persona) filter + created_at/generated_at
-- DESC ordering + LIMIT/OFFSET in one index scan; these composite indexes do,
-- keeping the list/count queries cheap as the tables grow.
--
-- Idempotent (IF NOT EXISTS) so re-applying the migration -- or running it
-- against a DB freshly built from schema.sql (which now carries the same two
-- statements) -- is a no-op. Applied by the orchestrator against the running
-- DB; not applied here.

CREATE INDEX IF NOT EXISTS idx_agent_notifications_org_persona_created ON agent_notifications (org_id, persona, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_reports_org_persona_generated ON agent_reports (org_id, persona, generated_at DESC);
