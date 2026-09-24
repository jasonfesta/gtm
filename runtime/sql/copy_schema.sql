CREATE TABLE IF NOT EXISTS copy_revisions (
    draft_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0),
    format TEXT NOT NULL CHECK(format IN ('email','dm','reply_dm')),
    variant TEXT NOT NULL CHECK(variant IN ('a','b')),
    channel TEXT NOT NULL CHECK(channel IN ('email','x','linkedin')),
    agent_id TEXT NOT NULL REFERENCES agents(agent_id),
    person_id TEXT REFERENCES people(person_id),
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    incoming_message TEXT NOT NULL DEFAULT '',
    evidence_ids_json TEXT NOT NULL,
    editor TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status='draft'),
    created_at TEXT NOT NULL,
    PRIMARY KEY(draft_id,revision)
);
CREATE TRIGGER IF NOT EXISTS copy_revisions_no_update BEFORE UPDATE ON copy_revisions
BEGIN SELECT RAISE(ABORT, 'copy revisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS copy_revisions_no_delete BEFORE DELETE ON copy_revisions
BEGIN SELECT RAISE(ABORT, 'copy revisions are append-only'); END;
