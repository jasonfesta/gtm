PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_meta(key, value) VALUES
    ('schema_version', '1'),
    ('created_at', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

CREATE TABLE IF NOT EXISTS accounts (
    account_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL DEFAULT 'linkedin' CHECK(platform = 'linkedin'),
    platform_member_urn TEXT,
    public_identifier TEXT,
    profile_url TEXT,
    display_name TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'America/New_York',
    status TEXT NOT NULL DEFAULT 'observed'
        CHECK(status IN ('observed', 'preview', 'active', 'paused', 'retired')),
    first_detected_at TEXT NOT NULL,
    last_detected_at TEXT NOT NULL,
    notes TEXT,
    UNIQUE(platform, platform_member_urn),
    UNIQUE(platform, public_identifier)
);

CREATE TABLE IF NOT EXISTS browser_profiles (
    browser_profile_key TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    active_account_id TEXT REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_identity_check TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS account_policies (
    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK(revision > 0),
    mode TEXT NOT NULL DEFAULT 'preview' CHECK(mode IN ('preview', 'approve', 'live', 'paused')),
    policy_json TEXT NOT NULL CHECK(json_valid(policy_json)),
    reviewed_by TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(account_id, revision)
);

CREATE TABLE IF NOT EXISTS people (
    person_id TEXT PRIMARY KEY,
    crm_person_id TEXT,
    canonical_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    identity_status TEXT NOT NULL DEFAULT 'candidate'
        CHECK(identity_status IN ('candidate', 'supported', 'verified', 'conflicting', 'merged')),
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_linkedin_people_name ON people(normalized_name);

CREATE TABLE IF NOT EXISTS profiles (
    profile_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES people(person_id) ON UPDATE CASCADE ON DELETE CASCADE,
    platform TEXT NOT NULL DEFAULT 'linkedin' CHECK(platform = 'linkedin'),
    platform_member_urn TEXT,
    public_identifier TEXT,
    profile_url TEXT NOT NULL,
    normalized_profile_url TEXT NOT NULL,
    display_name TEXT,
    current_company TEXT,
    current_role TEXT,
    identity_method TEXT NOT NULL DEFAULT 'candidate'
        CHECK(identity_method IN ('official_link', 'self_link', 'corroborated', 'candidate', 'manual_review')),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    notes TEXT,
    UNIQUE(platform, platform_member_urn),
    UNIQUE(platform, normalized_profile_url)
);

CREATE INDEX IF NOT EXISTS idx_linkedin_profiles_person ON profiles(person_id);

CREATE TABLE IF NOT EXISTS account_profile_state (
    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    profile_id TEXT NOT NULL REFERENCES profiles(profile_id) ON UPDATE CASCADE ON DELETE CASCADE,
    connection_degree TEXT CHECK(connection_degree IN ('1st', '2nd', '3rd', 'following', 'unknown')),
    message_available INTEGER CHECK(message_available IN (0, 1)),
    follows_profile INTEGER CHECK(follows_profile IN (0, 1)),
    observed_at TEXT NOT NULL,
    notes TEXT,
    PRIMARY KEY(account_id, profile_id)
);

CREATE TABLE IF NOT EXISTS posts (
    post_id TEXT PRIMARY KEY,
    author_profile_id TEXT REFERENCES profiles(profile_id) ON UPDATE CASCADE ON DELETE SET NULL,
    platform_post_id TEXT,
    canonical_url TEXT,
    post_signature TEXT NOT NULL,
    body TEXT,
    body_sha256 TEXT,
    posted_at TEXT,
    observed_at TEXT NOT NULL,
    post_kind TEXT NOT NULL DEFAULT 'unknown'
        CHECK(post_kind IN ('original', 'repost', 'comment', 'ad', 'job', 'unknown')),
    has_media INTEGER NOT NULL DEFAULT 0 CHECK(has_media IN (0, 1)),
    notes TEXT,
    UNIQUE(platform_post_id),
    UNIQUE(canonical_url),
    UNIQUE(post_signature)
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    account_id TEXT REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    browser_profile_key TEXT REFERENCES browser_profiles(browser_profile_key) ON UPDATE CASCADE ON DELETE SET NULL,
    source TEXT NOT NULL CHECK(source IN ('interactive', 'scheduled', 'import', 'reconcile')),
    workflow TEXT NOT NULL CHECK(workflow IN ('casual', 'lead', 'hiring', 'reply_dm', 'reconcile', 'import')),
    requested_count INTEGER CHECK(requested_count IS NULL OR requested_count >= 0),
    policy_revision INTEGER,
    status TEXT NOT NULL CHECK(status IN ('started', 'complete', 'partial', 'failed', 'cancelled', 'blocked')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    summary_json TEXT CHECK(summary_json IS NULL OR json_valid(summary_json)),
    error TEXT
);

CREATE TABLE IF NOT EXISTS import_sources (
    import_id TEXT PRIMARY KEY,
    account_id TEXT REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('legacy_log', 'owner_export', 'manual_conversation', 'permitted_integration')),
    source_path TEXT,
    source_sha256 TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    coverage_start TEXT,
    coverage_end TEXT,
    source_scope TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('staged', 'reviewed', 'accepted', 'rejected', 'partial')),
    counts_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(counts_json)),
    notes TEXT,
    UNIQUE(source_sha256, source_scope)
);

CREATE TABLE IF NOT EXISTS history_coverage (
    coverage_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    person_id TEXT REFERENCES people(person_id) ON UPDATE CASCADE ON DELETE CASCADE,
    surface TEXT NOT NULL CHECK(surface IN ('comments', 'dm', 'all')),
    coverage_status TEXT NOT NULL CHECK(coverage_status IN ('unknown', 'partial', 'complete')),
    covered_from TEXT,
    covered_through TEXT,
    import_id TEXT REFERENCES import_sources(import_id) ON UPDATE CASCADE ON DELETE SET NULL,
    reviewed_at TEXT,
    reviewed_by TEXT,
    notes TEXT,
    UNIQUE(account_id, person_id, surface, import_id)
);

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    person_id TEXT NOT NULL REFERENCES people(person_id) ON UPDATE CASCADE ON DELETE CASCADE,
    surface TEXT NOT NULL CHECK(surface IN ('post_comments', 'dm')),
    platform_thread_id TEXT,
    opened_at TEXT NOT NULL,
    last_activity_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'closed', 'blocked', 'archived')),
    notes TEXT,
    UNIQUE(account_id, surface, platform_thread_id)
);

CREATE TABLE IF NOT EXISTS draft_revisions (
    draft_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0),
    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    person_id TEXT REFERENCES people(person_id) ON UPDATE CASCADE ON DELETE SET NULL,
    post_id TEXT REFERENCES posts(post_id) ON UPDATE CASCADE ON DELETE SET NULL,
    conversation_id TEXT REFERENCES conversations(conversation_id) ON UPDATE CASCADE ON DELETE SET NULL,
    workflow TEXT NOT NULL CHECK(workflow IN ('casual', 'lead', 'hiring', 'reply_dm')),
    message_kind TEXT NOT NULL CHECK(message_kind IN ('comment', 'dm', 'reply')),
    variant TEXT NOT NULL DEFAULT 'a' CHECK(variant IN ('a', 'b', 'selected')),
    body TEXT NOT NULL,
    source_message_id TEXT,
    evidence_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(evidence_json)),
    lint_profile TEXT,
    lint_status TEXT NOT NULL DEFAULT 'not_checked' CHECK(lint_status IN ('not_checked', 'passed', 'failed')),
    editor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(draft_id, revision)
);

CREATE TRIGGER IF NOT EXISTS draft_revisions_no_update
BEFORE UPDATE ON draft_revisions BEGIN
    SELECT RAISE(ABORT, 'draft revisions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS draft_revisions_no_delete
BEFORE DELETE ON draft_revisions BEGIN
    SELECT RAISE(ABORT, 'draft revisions are append-only');
END;

CREATE TABLE IF NOT EXISTS suppressions (
    suppression_id TEXT PRIMARY KEY,
    account_id TEXT REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    person_id TEXT REFERENCES people(person_id) ON UPDATE CASCADE ON DELETE CASCADE,
    profile_id TEXT REFERENCES profiles(profile_id) ON UPDATE CASCADE ON DELETE CASCADE,
    action_type TEXT CHECK(action_type IN ('comment', 'dm', 'reply', 'connect')),
    reason TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    effective_at TEXT NOT NULL,
    expires_at TEXT,
    source TEXT,
    notes TEXT,
    CHECK(person_id IS NOT NULL OR profile_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_linkedin_suppressions
    ON suppressions(account_id, person_id, profile_id, is_active);

CREATE TABLE IF NOT EXISTS action_intents (
    action_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    run_id TEXT REFERENCES runs(run_id) ON UPDATE CASCADE ON DELETE SET NULL,
    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    person_id TEXT NOT NULL REFERENCES people(person_id) ON UPDATE CASCADE ON DELETE CASCADE,
    profile_id TEXT REFERENCES profiles(profile_id) ON UPDATE CASCADE ON DELETE SET NULL,
    post_id TEXT REFERENCES posts(post_id) ON UPDATE CASCADE ON DELETE SET NULL,
    conversation_id TEXT REFERENCES conversations(conversation_id) ON UPDATE CASCADE ON DELETE SET NULL,
    draft_id TEXT,
    action_type TEXT NOT NULL CHECK(action_type IN ('comment', 'dm', 'reply', 'connect')),
    workflow TEXT NOT NULL CHECK(workflow IN ('casual', 'lead', 'hiring', 'reply_dm')),
    policy_scope TEXT NOT NULL,
    reserved_at TEXT NOT NULL,
    expires_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json))
);

CREATE INDEX IF NOT EXISTS idx_linkedin_action_person
    ON action_intents(account_id, person_id, action_type, workflow, reserved_at DESC);

CREATE TABLE IF NOT EXISTS action_events (
    event_id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL REFERENCES action_intents(action_id) ON UPDATE CASCADE ON DELETE CASCADE,
    state TEXT NOT NULL CHECK(state IN ('reserved', 'attempted', 'sent', 'failed', 'uncertain', 'skipped', 'cancelled', 'reconciled')),
    occurred_at TEXT NOT NULL,
    platform_reference TEXT,
    error_class TEXT,
    error_message TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json))
);

CREATE INDEX IF NOT EXISTS idx_linkedin_action_events
    ON action_events(action_id, occurred_at);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    person_id TEXT NOT NULL REFERENCES people(person_id) ON UPDATE CASCADE ON DELETE CASCADE,
    conversation_id TEXT REFERENCES conversations(conversation_id) ON UPDATE CASCADE ON DELETE SET NULL,
    post_id TEXT REFERENCES posts(post_id) ON UPDATE CASCADE ON DELETE SET NULL,
    action_id TEXT REFERENCES action_intents(action_id) ON UPDATE CASCADE ON DELETE SET NULL,
    platform_message_id TEXT,
    idempotency_key TEXT,
    direction TEXT NOT NULL CHECK(direction IN ('inbound', 'outbound')),
    message_kind TEXT NOT NULL CHECK(message_kind IN ('casual_comment', 'lead_comment', 'hiring_comment', 'dm', 'dm_reply', 'comment_reply')),
    body TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    in_reply_to_message_id TEXT REFERENCES messages(message_id) ON UPDATE CASCADE ON DELETE SET NULL,
    raw_capture_path TEXT,
    source TEXT NOT NULL CHECK(source IN ('live', 'import', 'reconcile', 'manual')),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json)),
    UNIQUE(account_id, platform_message_id),
    UNIQUE(account_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_linkedin_message_history
    ON messages(account_id, person_id, occurred_at DESC);

CREATE TRIGGER IF NOT EXISTS messages_no_update
BEFORE UPDATE ON messages BEGIN
    SELECT RAISE(ABORT, 'messages are append-only');
END;

CREATE TRIGGER IF NOT EXISTS messages_no_delete
BEFORE DELETE ON messages BEGIN
    SELECT RAISE(ABORT, 'messages are append-only');
END;

CREATE TABLE IF NOT EXISTS message_status_events (
    status_event_id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(message_id) ON UPDATE CASCADE ON DELETE CASCADE,
    status TEXT NOT NULL CHECK(status IN ('observed', 'reported_sent', 'sent', 'delivered', 'failed', 'deleted_on_platform', 'reconciled', 'unknown')),
    occurred_at TEXT NOT NULL,
    detail TEXT,
    source TEXT NOT NULL CHECK(source IN ('live', 'import', 'reconcile', 'manual'))
);

CREATE TABLE IF NOT EXISTS run_items (
    run_item_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON UPDATE CASCADE ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    person_id TEXT REFERENCES people(person_id) ON UPDATE CASCADE ON DELETE SET NULL,
    profile_id TEXT REFERENCES profiles(profile_id) ON UPDATE CASCADE ON DELETE SET NULL,
    post_id TEXT REFERENCES posts(post_id) ON UPDATE CASCADE ON DELETE SET NULL,
    draft_id TEXT,
    action_id TEXT REFERENCES action_intents(action_id) ON UPDATE CASCADE ON DELETE SET NULL,
    decision TEXT NOT NULL CHECK(decision IN ('candidate', 'eligible', 'drafted', 'reserved', 'sent', 'failed', 'skipped', 'needs_review')),
    reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, ordinal)
);

CREATE TABLE IF NOT EXISTS confirmed_send_receipts (
    receipt_id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL UNIQUE REFERENCES messages(message_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    action_id TEXT NOT NULL UNIQUE REFERENCES action_intents(action_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    platform_reference TEXT NOT NULL,
    confirmation_method TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    source_run_id TEXT REFERENCES runs(run_id) ON UPDATE CASCADE ON DELETE SET NULL,
    UNIQUE(account_id, platform_reference)
);

CREATE TABLE IF NOT EXISTS analytics_outbox (
    event_id TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL UNIQUE REFERENCES confirmed_send_receipts(receipt_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    source_event_id TEXT NOT NULL UNIQUE,
    event_name TEXT NOT NULL DEFAULT 'gtm.message_sent',
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending', 'exporting', 'accepted', 'confirmed', 'failed', 'discarded')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    last_attempt_at TEXT,
    accepted_at TEXT,
    confirmed_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_linkedin_analytics_outbox_state
    ON analytics_outbox(state, created_at);

CREATE VIEW IF NOT EXISTS v_person_message_history AS
SELECT
    m.account_id,
    m.person_id,
    p.canonical_name,
    m.direction,
    m.message_kind,
    m.body,
    m.occurred_at,
    m.platform_message_id,
    m.post_id,
    m.conversation_id
FROM messages m
JOIN people p ON p.person_id = m.person_id
ORDER BY m.account_id, m.person_id, m.occurred_at;

CREATE VIEW IF NOT EXISTS v_last_outbound_touch AS
SELECT
    account_id,
    person_id,
    MAX(occurred_at) AS last_outbound_at,
    MAX(CASE WHEN message_kind = 'casual_comment' THEN occurred_at END) AS last_casual_comment_at,
    MAX(CASE WHEN message_kind IN ('lead_comment', 'hiring_comment') THEN occurred_at END) AS last_lead_comment_at,
    MAX(CASE WHEN message_kind IN ('dm', 'dm_reply') THEN occurred_at END) AS last_dm_at,
    COUNT(*) AS outbound_count
FROM messages
WHERE direction = 'outbound'
GROUP BY account_id, person_id;

CREATE VIEW IF NOT EXISTS v_latest_action_state AS
SELECT ai.action_id, ai.idempotency_key, ai.account_id, ai.person_id, ai.action_type, ai.workflow,
       ae.state, ae.occurred_at, ae.platform_reference, ae.error_class, ae.error_message
FROM action_intents ai
LEFT JOIN action_events ae ON ae.event_id = (
    SELECT event_id FROM action_events newer
    WHERE newer.action_id = ai.action_id
    ORDER BY newer.occurred_at DESC, newer.event_id DESC
    LIMIT 1
);

INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', '2');
