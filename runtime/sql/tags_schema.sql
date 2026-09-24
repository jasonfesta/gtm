CREATE TABLE IF NOT EXISTS crm_tags (
    tag_id TEXT PRIMARY KEY,
    label TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('audience', 'role'))
);

INSERT OR IGNORE INTO crm_tags(tag_id, label, kind) VALUES
    ('assistant_developers', 'assistant developers', 'audience'),
    ('enterprise_developers', 'enterprise developers', 'audience'),
    ('open_source_developers', 'open source developers', 'audience'),
    ('developer_infra_platforms', 'developer infra platforms', 'audience'),
    ('solo_developer', 'solo developer', 'role'),
    ('founder', 'founder', 'role');

CREATE TABLE IF NOT EXISTS person_tag_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES people(person_id),
    tag_id TEXT NOT NULL REFERENCES crm_tags(tag_id),
    action TEXT NOT NULL CHECK (action IN ('add', 'remove')),
    evidence TEXT NOT NULL CHECK (length(trim(evidence)) > 0),
    reviewer TEXT NOT NULL CHECK (length(trim(reviewer)) > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE VIEW IF NOT EXISTS current_person_tags AS
SELECT e.*, t.label, t.kind FROM person_tag_events e
JOIN crm_tags t USING(tag_id)
WHERE e.action = 'add' AND e.event_id = (
    SELECT max(newer.event_id) FROM person_tag_events newer
    WHERE newer.person_id = e.person_id AND newer.tag_id = e.tag_id
);

CREATE TRIGGER IF NOT EXISTS person_tag_events_no_update
BEFORE UPDATE ON person_tag_events BEGIN
    SELECT RAISE(ABORT, 'tag history is append-only');
END;
CREATE TRIGGER IF NOT EXISTS person_tag_events_no_delete
BEFORE DELETE ON person_tag_events BEGIN
    SELECT RAISE(ABORT, 'tag history is append-only');
END;
