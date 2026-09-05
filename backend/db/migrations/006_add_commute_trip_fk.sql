-- BUGFIX (found live via the chat SQL agent): a user asked "how many rides
-- with female and no escorts" and the agent replied that "the database
-- schema lacks a link between trips and individual employee gender
-- records" -- wrong (trip -> commute -> employee is a real, working join
-- this app's own detectors use, e.g. detect_escort_compliance_signal), but
-- for a real reason: mis.commute.trip_id is a genuine column with no
-- declared FOREIGN KEY constraint to mis.trip(id) (only employee_id and
-- route_id have FKs). The SQL agent's schema context comes from
-- LangChain's SQLDatabase.get_table_info(), which surfaces relationships
-- via declared FK constraints -- with none declared here, the join is
-- structurally invisible to any FK-based schema introspection, not just a
-- reasoning gap in a smaller model. Verified live: zero orphaned
-- commute.trip_id values (every non-null value already references a real
-- trip row), so this is safe to declare, not just a documentation fix.
--
-- Additive-only, safe to re-run (guarded by a catalog check since Postgres
-- has no native `ADD CONSTRAINT IF NOT EXISTS`).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'commute_trip_id_fkey'
    ) THEN
        ALTER TABLE mis.commute
            ADD CONSTRAINT commute_trip_id_fkey FOREIGN KEY (trip_id) REFERENCES mis.trip(id);
    END IF;
END $$;
