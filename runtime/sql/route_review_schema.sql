-- Append-only person/channel decisions; original research evidence stays intact.
CREATE TABLE IF NOT EXISTS route_review_runs (
    run_id TEXT PRIMARY KEY,
    input_sha256 TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    reviewer TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS route_reviews (
    review_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES route_review_runs(run_id),
    person_id TEXT NOT NULL REFERENCES people(person_id),
    qualification TEXT NOT NULL CHECK(qualification IN ('qualified','adjacent','unresolved')),
    role_status TEXT NOT NULL CHECK(role_status IN ('supported','historical','uncertain','client')),
    outreach_score INTEGER CHECK(outreach_score IN (1,2,3)),
    score_status TEXT NOT NULL CHECK(score_status IN ('supported','provisional','unassigned')),
    readiness TEXT NOT NULL CHECK(readiness IN ('research_needed','review_needed','hold','suppressed','ready_for_preparation')),
    decision_json TEXT NOT NULL,
    UNIQUE(run_id,person_id)
);
CREATE TABLE IF NOT EXISTS channel_reviews (
    run_id TEXT NOT NULL,
    person_id TEXT NOT NULL,
    channel TEXT NOT NULL CHECK(channel IN ('email','linkedin','x')),
    outcome TEXT NOT NULL CHECK(outcome IN ('association_supported','candidate_only','not_found_in_checked_sources','held')),
    decision_json TEXT NOT NULL,
    PRIMARY KEY(run_id,person_id,channel),
    FOREIGN KEY(run_id,person_id) REFERENCES route_reviews(run_id,person_id)
);
CREATE TABLE IF NOT EXISTS route_review_changes (
    change_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES route_review_runs(run_id),
    table_name TEXT NOT NULL,
    record_id TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT NOT NULL
);
CREATE VIEW IF NOT EXISTS latest_route_reviews AS
SELECT r.*, u.reviewed_at, u.reviewer FROM route_reviews r
JOIN route_review_runs u USING(run_id)
WHERE r.review_id=(SELECT max(s.review_id) FROM route_reviews s WHERE s.person_id=r.person_id);
CREATE TRIGGER IF NOT EXISTS route_runs_no_update BEFORE UPDATE ON route_review_runs BEGIN SELECT RAISE(ABORT,'append-only review'); END;
CREATE TRIGGER IF NOT EXISTS route_runs_no_delete BEFORE DELETE ON route_review_runs BEGIN SELECT RAISE(ABORT,'append-only review'); END;
CREATE TRIGGER IF NOT EXISTS route_reviews_no_update BEFORE UPDATE ON route_reviews BEGIN SELECT RAISE(ABORT,'append-only review'); END;
CREATE TRIGGER IF NOT EXISTS route_reviews_no_delete BEFORE DELETE ON route_reviews BEGIN SELECT RAISE(ABORT,'append-only review'); END;
CREATE TRIGGER IF NOT EXISTS channel_reviews_no_update BEFORE UPDATE ON channel_reviews BEGIN SELECT RAISE(ABORT,'append-only review'); END;
CREATE TRIGGER IF NOT EXISTS channel_reviews_no_delete BEFORE DELETE ON channel_reviews BEGIN SELECT RAISE(ABORT,'append-only review'); END;
CREATE TRIGGER IF NOT EXISTS route_changes_no_update BEFORE UPDATE ON route_review_changes BEGIN SELECT RAISE(ABORT,'append-only review'); END;
CREATE TRIGGER IF NOT EXISTS route_changes_no_delete BEFORE DELETE ON route_review_changes BEGIN SELECT RAISE(ABORT,'append-only review'); END;
