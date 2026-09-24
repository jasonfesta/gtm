-- Additive human/agent CRM. Existing people, agents and outreach history remain authoritative.
CREATE TABLE IF NOT EXISTS relationship_profiles (
    profile_id TEXT PRIMARY KEY,
    person_id TEXT UNIQUE REFERENCES people(person_id),
    agent_id TEXT UNIQUE REFERENCES agents(agent_id),
    audience TEXT,
    owner_person_id TEXT REFERENCES people(person_id),
    can_receive_requests TEXT NOT NULL DEFAULT 'unknown' CHECK(can_receive_requests IN ('yes','no','unknown')),
    can_call_external_apis TEXT NOT NULL DEFAULT 'unknown' CHECK(can_call_external_apis IN ('yes','no','unknown')),
    can_connect_mcp TEXT NOT NULL DEFAULT 'unknown' CHECK(can_connect_mcp IN ('yes','no','unknown')),
    can_install_integrations TEXT NOT NULL DEFAULT 'unknown' CHECK(can_install_integrations IN ('yes','no','unknown')),
    owner_approval_required TEXT NOT NULL DEFAULT 'unknown' CHECK(owner_approval_required IN ('yes','no','unknown')),
    evidence TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((person_id IS NOT NULL AND agent_id IS NULL AND profile_id='human:' || person_id
            AND (audience IS NULL OR audience IN ('personal_agent_owners','assistant_developers','early_adopters')))
        OR (agent_id IS NOT NULL AND person_id IS NULL AND profile_id='agent:' || agent_id
            AND (audience IS NULL OR audience IN ('personal_agents','assistant_agents','research_agents'))))
);

CREATE TABLE IF NOT EXISTS relationship_contacts (
    contact_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES relationship_profiles(profile_id),
    channel TEXT NOT NULL CHECK(channel IN ('github','reddit','x','linkedin','email','hacker-news','agent_email','agentdm','masumi','agentlist','a2a','agents_breakroom','moltbook','webmcp','discord')),
    address TEXT NOT NULL DEFAULT '',
    availability TEXT NOT NULL DEFAULT 'unknown' CHECK(availability IN ('unknown','available','unavailable')),
    provider TEXT,
    agent_card_url TEXT,
    authentication_requirements TEXT,
    supported_interactions TEXT,
    supports_dm TEXT NOT NULL DEFAULT 'unknown' CHECK(supports_dm IN ('yes','no','unknown')),
    agent_operated INTEGER CHECK(agent_operated IN (0,1)),
    agent_operated_evidence_url TEXT,
    agent_operated_verified_at TEXT,
    source_url TEXT NOT NULL,
    last_verified_at TEXT,
    legacy_contact_id TEXT REFERENCES contact_points(contact_id),
    updated_at TEXT NOT NULL,
    UNIQUE(profile_id,channel,address),
    CHECK(availability <> 'available' OR (length(trim(address)) > 0 AND last_verified_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_relationship_contacts_profile ON relationship_contacts(profile_id);

-- Observations only: no planned messages, drafts or inferred installations.
CREATE TABLE IF NOT EXISTS relationship_events (
    event_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES relationship_profiles(profile_id),
    channel TEXT NOT NULL CHECK(channel IN ('github','reddit','x','linkedin','email','hacker-news','agent_email','agentdm','masumi','agentlist','a2a','agents_breakroom','moltbook','webmcp','discord')),
    kind TEXT NOT NULL CHECK(kind IN ('like','comment','reply','outbound','installed','mcp_connected','search','repeat_usage','referral')),
    account_key TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    occurred_at TEXT,
    observed_at TEXT NOT NULL,
    target_is_ours INTEGER NOT NULL DEFAULT 0 CHECK(target_is_ours IN (0,1)),
    target_url TEXT,
    interaction_url TEXT,
    external_reference TEXT,
    evidence TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    UNIQUE(channel,account_key,provider_id,kind)
);
CREATE INDEX IF NOT EXISTS idx_relationship_events_profile ON relationship_events(profile_id,kind,occurred_at);
CREATE TRIGGER IF NOT EXISTS relationship_events_no_update BEFORE UPDATE ON relationship_events BEGIN
    SELECT RAISE(ABORT, 'relationship history is append-only');
END;
CREATE TRIGGER IF NOT EXISTS relationship_events_no_delete BEFORE DELETE ON relationship_events BEGIN
    SELECT RAISE(ABORT, 'relationship history is append-only');
END;

-- Relationship routes have their own FK; legacy contact_points suppressions stay intact.
CREATE TABLE IF NOT EXISTS relationship_contact_suppressions (
    suppression_id TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL REFERENCES relationship_contacts(contact_id),
    channel TEXT,
    reason TEXT NOT NULL CHECK(reason IN ('opt_out','do_not_contact','invalid_contact','privacy_request','legal_restriction','other')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    effective_at TEXT NOT NULL,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_relationship_suppressions_contact
    ON relationship_contact_suppressions(contact_id,is_active);
