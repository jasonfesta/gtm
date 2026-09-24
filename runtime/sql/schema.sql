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

CREATE TABLE IF NOT EXISTS companies (
    company_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    legal_name TEXT,
    domain TEXT,
    website_url TEXT,
    linkedin_url TEXT,
    x_url TEXT,
    company_status TEXT NOT NULL DEFAULT 'active'
        CHECK (company_status IN ('active', 'inactive', 'acquired', 'unknown')),
    research_status TEXT NOT NULL DEFAULT 'discovered'
        CHECK (research_status IN ('discovered', 'identity_pending', 'founder_research_pending', 'contact_research_pending', 'review_needed', 'complete')),
    confidence INTEGER NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 100),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(normalized_name, domain)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_domain
    ON companies(lower(domain)) WHERE domain IS NOT NULL;

CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    company_id TEXT REFERENCES companies(company_id) ON UPDATE CASCADE ON DELETE SET NULL,
    canonical_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    agent_kind TEXT NOT NULL DEFAULT 'agent'
        CHECK (agent_kind IN ('agent', 'infrastructure_provider', 'unknown')),
    description TEXT,
    website_url TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'inactive', 'unknown')),
    research_status TEXT NOT NULL DEFAULT 'discovered'
        CHECK (research_status IN ('discovered', 'company_pending', 'identity_pending', 'review_needed', 'complete')),
    confidence INTEGER NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 100),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_agents_normalized_name ON agents(normalized_name);
CREATE INDEX IF NOT EXISTS idx_agents_company_id ON agents(company_id);

CREATE TABLE IF NOT EXISTS people (
    person_id TEXT PRIMARY KEY,
    primary_company_id TEXT REFERENCES companies(company_id) ON UPDATE CASCADE ON DELETE SET NULL,
    full_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    current_role TEXT,
    founder_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (founder_status IN ('founder', 'cofounder', 'founding_team', 'not_founder', 'unknown')),
    identity_status TEXT NOT NULL DEFAULT 'unverified'
        CHECK (identity_status IN ('unverified', 'single_source', 'corroborated', 'conflicting', 'verified')),
    research_status TEXT NOT NULL DEFAULT 'identity_pending'
        CHECK (research_status IN ('identity_pending', 'profile_research_pending', 'email_research_pending', 'review_needed', 'complete')),
    confidence INTEGER NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 100),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_people_normalized_name ON people(normalized_name);

CREATE TABLE IF NOT EXISTS company_people (
    company_id TEXT NOT NULL REFERENCES companies(company_id) ON UPDATE CASCADE ON DELETE CASCADE,
    person_id TEXT NOT NULL REFERENCES people(person_id) ON UPDATE CASCADE ON DELETE CASCADE,
    role TEXT,
    relationship_type TEXT NOT NULL DEFAULT 'team'
        CHECK (relationship_type IN ('founder', 'cofounder', 'founding_team', 'executive', 'team', 'former')),
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    start_date TEXT,
    end_date TEXT,
    confidence INTEGER NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 100),
    PRIMARY KEY (company_id, person_id, relationship_type)
);

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    title TEXT,
    publisher TEXT,
    source_type TEXT NOT NULL
        CHECK (source_type IN ('directory', 'official_site', 'founder_bio', 'announcement', 'corporate_record', 'linkedin', 'x', 'news', 'email_page', 'other')),
    quality_tier INTEGER NOT NULL CHECK (quality_tier BETWEEN 1 AND 5),
    published_at TEXT,
    accessed_at TEXT NOT NULL,
    content_sha256 TEXT,
    archived_path TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS listings (
    listing_id TEXT PRIMARY KEY,
    source_site TEXT NOT NULL CHECK (source_site IN ('assistantbenchmark', 'imessage_store')),
    source_key TEXT NOT NULL,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON UPDATE CASCADE ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(source_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    source_name TEXT NOT NULL,
    source_description TEXT,
    source_category TEXT,
    official_url TEXT,
    listing_kind TEXT NOT NULL DEFAULT 'agent'
        CHECK (listing_kind IN ('agent', 'infrastructure_provider', 'unknown')),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    raw_record_json TEXT,
    UNIQUE(source_site, source_key)
);

CREATE INDEX IF NOT EXISTS idx_listings_agent_id ON listings(agent_id);

CREATE TABLE IF NOT EXISTS discovery_candidates (
    candidate_id TEXT PRIMARY KEY,
    source_site TEXT NOT NULL CHECK (source_site IN ('assistantbenchmark', 'imessage_store')),
    source_key TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_name TEXT,
    source_description TEXT,
    source_category TEXT,
    official_url TEXT,
    listing_kind TEXT NOT NULL DEFAULT 'unknown'
        CHECK (listing_kind IN ('agent', 'infrastructure_provider', 'unknown')),
    raw_record_json TEXT NOT NULL,
    candidate_status TEXT NOT NULL DEFAULT 'new'
        CHECK (candidate_status IN ('new', 'auto_matched', 'needs_review', 'approved', 'rejected')),
    proposed_agent_id TEXT REFERENCES agents(agent_id) ON UPDATE CASCADE ON DELETE SET NULL,
    run_id TEXT,
    discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(source_site, source_key)
);

CREATE INDEX IF NOT EXISTS idx_discovery_candidates_queue
    ON discovery_candidates(candidate_status, source_site, source_key);

CREATE TABLE IF NOT EXISTS aliases (
    alias_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('agent', 'company', 'person')),
    entity_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    source_id TEXT REFERENCES sources(source_id) ON UPDATE CASCADE ON DELETE SET NULL,
    notes TEXT,
    UNIQUE(entity_type, entity_id, normalized_alias)
);

CREATE INDEX IF NOT EXISTS idx_aliases_lookup ON aliases(entity_type, normalized_alias);

CREATE TABLE IF NOT EXISTS contact_points (
    contact_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES people(person_id) ON UPDATE CASCADE ON DELETE CASCADE,
    contact_type TEXT NOT NULL CHECK (contact_type IN ('email', 'linkedin', 'x', 'website', 'other')),
    value TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    verification_status TEXT NOT NULL DEFAULT 'unverified'
        CHECK (verification_status IN ('confirmed', 'inferred', 'pattern_based', 'unverified', 'invalid', 'stale')),
    verification_method TEXT,
    confidence INTEGER NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 100),
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    is_public INTEGER NOT NULL DEFAULT 1 CHECK (is_public IN (0, 1)),
    source_id TEXT REFERENCES sources(source_id) ON UPDATE CASCADE ON DELETE SET NULL,
    first_seen_at TEXT NOT NULL,
    last_verified_at TEXT,
    notes TEXT,
    UNIQUE(person_id, contact_type, normalized_value)
);

CREATE INDEX IF NOT EXISTS idx_contact_points_person ON contact_points(person_id);
CREATE INDEX IF NOT EXISTS idx_contact_points_lookup ON contact_points(contact_type, normalized_value);

CREATE TABLE IF NOT EXISTS evidence_claims (
    claim_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('agent', 'company', 'person', 'contact_point', 'listing')),
    entity_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    claim_value TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES sources(source_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    evidence_relation TEXT NOT NULL DEFAULT 'supports'
        CHECK (evidence_relation IN ('supports', 'contradicts', 'mentions')),
    verification_status TEXT NOT NULL DEFAULT 'unreviewed'
        CHECK (verification_status IN ('unreviewed', 'accepted', 'rejected', 'superseded')),
    confidence INTEGER NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    excerpt TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_claims_entity ON evidence_claims(entity_type, entity_id, field_name);

CREATE TABLE IF NOT EXISTS conflicts (
    conflict_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('agent', 'company', 'person', 'contact_point', 'listing')),
    entity_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    value_a TEXT NOT NULL,
    source_a_id TEXT REFERENCES sources(source_id) ON UPDATE CASCADE ON DELETE SET NULL,
    value_b TEXT NOT NULL,
    source_b_id TEXT REFERENCES sources(source_id) ON UPDATE CASCADE ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'accepted_ambiguity')),
    resolution TEXT,
    resolved_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS research_tasks (
    task_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('agent', 'company', 'person', 'contact_point')),
    entity_id TEXT NOT NULL,
    task_type TEXT NOT NULL
        CHECK (task_type IN ('identify_company', 'identify_founders', 'verify_identity', 'find_profiles', 'find_email', 'verify_email', 'resolve_conflict', 'quality_review')),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'in_progress', 'blocked', 'needs_review', 'complete', 'not_found')),
    priority INTEGER NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    next_attempt_at TEXT,
    blocker TEXT,
    assigned_to TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_research_tasks_queue
    ON research_tasks(status, priority DESC, next_attempt_at);

CREATE TABLE IF NOT EXISTS merge_events (
    merge_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('agent', 'company', 'person')),
    surviving_entity_id TEXT NOT NULL,
    merged_entity_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_json TEXT,
    reversible_snapshot_json TEXT NOT NULL,
    merged_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    merged_by TEXT NOT NULL DEFAULT 'local_user'
);

CREATE TABLE IF NOT EXISTS outreach_events (
    event_id TEXT PRIMARY KEY,
    person_id TEXT REFERENCES people(person_id) ON UPDATE CASCADE ON DELETE SET NULL,
    company_id TEXT REFERENCES companies(company_id) ON UPDATE CASCADE ON DELETE SET NULL,
    channel TEXT NOT NULL CHECK (channel IN ('email', 'linkedin', 'x')),
    direction TEXT NOT NULL CHECK (direction IN ('outbound', 'inbound')),
    occurred_at TEXT NOT NULL,
    outcome TEXT NOT NULL DEFAULT 'sent'
        CHECK (outcome IN ('drafted', 'sent', 'delivered', 'replied', 'bounced', 'declined', 'opted_out', 'unknown')),
    template_id TEXT,
    external_reference TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (person_id IS NOT NULL OR company_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_outreach_person_channel_date
    ON outreach_events(person_id, channel, occurred_at DESC);

-- Public, body-free X notification history. Unlike outreach_events, this table
-- can retain an interaction before the actor has been resolved to a CRM person.
CREATE TABLE IF NOT EXISTS x_engagement_events (
    event_id TEXT PRIMARY KEY,
    account TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    interaction TEXT NOT NULL CHECK (interaction IN ('like', 'reply')),
    actor_handle TEXT NOT NULL,
    actor_profile_url TEXT NOT NULL,
    target_url TEXT NOT NULL,
    interaction_url TEXT,
    occurred_at TEXT NOT NULL,
    person_id TEXT REFERENCES people(person_id) ON UPDATE CASCADE ON DELETE SET NULL,
    identity_status TEXT NOT NULL CHECK (identity_status IN ('matched', 'unmatched', 'conflict')),
    target_status TEXT NOT NULL CHECK (target_status IN ('registered', 'unregistered', 'conflict')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(account, provider_id)
);

CREATE INDEX IF NOT EXISTS idx_x_engagement_actor_date
    ON x_engagement_events(actor_handle, occurred_at DESC);

CREATE TABLE IF NOT EXISTS suppressions (
    suppression_id TEXT PRIMARY KEY,
    person_id TEXT REFERENCES people(person_id) ON UPDATE CASCADE ON DELETE CASCADE,
    company_id TEXT REFERENCES companies(company_id) ON UPDATE CASCADE ON DELETE CASCADE,
    contact_id TEXT REFERENCES contact_points(contact_id) ON UPDATE CASCADE ON DELETE CASCADE,
    channel TEXT CHECK (channel IN ('email', 'linkedin', 'x')),
    reason TEXT NOT NULL
        CHECK (reason IN ('opt_out', 'do_not_contact', 'invalid_contact', 'privacy_request', 'legal_restriction', 'other')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    source_id TEXT REFERENCES sources(source_id) ON UPDATE CASCADE ON DELETE SET NULL,
    effective_at TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (person_id IS NOT NULL OR company_id IS NOT NULL OR contact_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_suppressions_active_person
    ON suppressions(person_id, is_active, channel);

CREATE TABLE IF NOT EXISTS message_templates (
    template_id TEXT PRIMARY KEY,
    channel TEXT NOT NULL CHECK (channel IN ('email', 'linkedin', 'x')),
    variant TEXT NOT NULL CHECK (variant IN ('A', 'B')),
    name TEXT NOT NULL,
    subject_template TEXT,
    body_template TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'retired')),
    required_fields_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(channel, variant, name)
);

CREATE TABLE IF NOT EXISTS draft_messages (
    draft_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES people(person_id) ON UPDATE CASCADE ON DELETE CASCADE,
    template_id TEXT NOT NULL REFERENCES message_templates(template_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    channel TEXT NOT NULL CHECK (channel IN ('email', 'linkedin', 'x')),
    subject TEXT,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'needs_review', 'approved', 'retired')),
    personalization_evidence_json TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS crawl_runs (
    run_id TEXT PRIMARY KEY,
    source_site TEXT NOT NULL CHECK (source_site IN ('assistantbenchmark', 'imessage_store', 'research')),
    mode TEXT NOT NULL CHECK (mode IN ('pilot', 'incremental', 'full', 'manual')),
    status TEXT NOT NULL CHECK (status IN ('started', 'complete', 'partial', 'failed', 'cancelled')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    request_count INTEGER NOT NULL DEFAULT 0,
    records_discovered INTEGER NOT NULL DEFAULT 0,
    records_changed INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    checkpoint_json TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS fetches (
    fetch_id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES crawl_runs(run_id) ON UPDATE CASCADE ON DELETE SET NULL,
    url TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    http_status INTEGER,
    content_type TEXT,
    content_sha256 TEXT,
    archived_path TEXT,
    robots_allowed INTEGER CHECK (robots_allowed IN (0, 1)),
    attempt INTEGER NOT NULL DEFAULT 1,
    retry_after_seconds INTEGER,
    error_class TEXT,
    error_message TEXT,
    duration_ms INTEGER,
    UNIQUE(run_id, url, attempt)
);

CREATE VIEW IF NOT EXISTS v_contact_crm AS
SELECT
    p.person_id,
    p.full_name,
    p.current_role,
    p.founder_status,
    c.company_id,
    c.canonical_name AS company_name,
    MAX(CASE WHEN cp.contact_type = 'email' AND cp.is_primary = 1 THEN cp.value END) AS email,
    MAX(CASE WHEN cp.contact_type = 'email' AND cp.is_primary = 1 THEN cp.verification_status END) AS email_status,
    MAX(CASE WHEN cp.contact_type = 'linkedin' AND cp.is_primary = 1 THEN cp.value END) AS linkedin_url,
    MAX(CASE WHEN cp.contact_type = 'x' AND cp.is_primary = 1 THEN cp.value END) AS x_url,
    CASE WHEN EXISTS (
        SELECT 1 FROM outreach_events oe
        WHERE oe.person_id = p.person_id AND oe.direction = 'outbound' AND oe.outcome <> 'drafted'
    ) THEN 1 ELSE 0 END AS contacted,
    (SELECT MAX(occurred_at) FROM outreach_events oe WHERE oe.person_id = p.person_id AND oe.direction = 'outbound' AND oe.channel = 'x' AND oe.outcome <> 'drafted') AS last_contacted_x,
    (SELECT MAX(occurred_at) FROM outreach_events oe WHERE oe.person_id = p.person_id AND oe.direction = 'outbound' AND oe.channel = 'linkedin' AND oe.outcome <> 'drafted') AS last_contacted_linkedin,
    (SELECT MAX(occurred_at) FROM outreach_events oe WHERE oe.person_id = p.person_id AND oe.direction = 'outbound' AND oe.channel = 'email' AND oe.outcome <> 'drafted') AS last_contacted_email,
    CASE WHEN EXISTS (
        SELECT 1 FROM suppressions s
        WHERE s.is_active = 1
          AND (s.person_id = p.person_id OR s.company_id = p.primary_company_id)
    ) THEN 1 ELSE 0 END AS suppressed,
    p.identity_status,
    p.research_status,
    p.confidence
FROM people p
LEFT JOIN companies c ON c.company_id = p.primary_company_id
LEFT JOIN contact_points cp ON cp.person_id = p.person_id
GROUP BY p.person_id;

CREATE VIEW IF NOT EXISTS v_review_queue AS
SELECT
    'task' AS queue_type,
    task_id AS item_id,
    entity_type,
    entity_id,
    task_type AS issue,
    status,
    priority,
    blocker AS detail,
    created_at
FROM research_tasks
WHERE status IN ('queued', 'blocked', 'needs_review')
UNION ALL
SELECT
    'conflict' AS queue_type,
    conflict_id AS item_id,
    entity_type,
    entity_id,
    field_name AS issue,
    status,
    100 AS priority,
    value_a || ' <> ' || value_b AS detail,
    created_at
FROM conflicts
WHERE status = 'open';
