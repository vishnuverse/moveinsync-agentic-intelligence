-- SP-B false-positive feedback loop (plan §7). Adds the feedback columns to
-- agent_notifications on an ALREADY-SEEDED volume -- a fresh db built from
-- db/schema.sql already has these (schema.sql's own agent_notifications
-- definition was updated to match, same convention as 002's composite index
-- being back-ported into schema.sql). Additive-only (IF NOT EXISTS), safe to
-- re-run. Applied by the orchestrator against the running DB; not applied by
-- db/seed/entrypoint.sh (which only special-cases 001, because 001 depends
-- on post-ingest stg.* tables being freshly rebuilt -- this migration has no
-- such dependency and is a plain, once-ever ALTER).
--
-- Deliberately NOT a new `status` CHECK value: `status` represents workflow
-- lifecycle (open/acked/needs_intervention/resolved); false-positive-ness is
-- an orthogonal quality judgment about whether the alert was even valid, not
-- a lifecycle stage. Marking false-positive sets is_false_positive=true AND
-- transitions status to the existing 'resolved' value, so no CHECK-constraint
-- change (DROP/ADD CONSTRAINT) is needed at all.

ALTER TABLE agent_notifications ADD COLUMN IF NOT EXISTS is_false_positive BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE agent_notifications ADD COLUMN IF NOT EXISTS false_positive_note TEXT;
ALTER TABLE agent_notifications ADD COLUMN IF NOT EXISTS false_positive_marked_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_agent_notifications_false_positive
    ON agent_notifications (org_id, is_false_positive) WHERE is_false_positive;
