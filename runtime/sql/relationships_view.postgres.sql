CREATE OR REPLACE VIEW v_relationship_crm AS
WITH identities AS (
    SELECT 'human:' || person_id AS profile_id, 'human'::text AS entity_type,
        person_id AS entity_id, full_name AS name, primary_company_id AS company_id,
        "current_role", founder_status, identity_status, research_status, confidence, created_at, updated_at
    FROM people
    UNION ALL
    SELECT 'agent:' || agent_id, 'agent', agent_id, canonical_name, company_id,
        NULL, NULL, NULL, research_status, confidence, created_at, updated_at
    FROM agents
), contacts AS (
    SELECT profile_id, contact_id, channel, address, availability, supports_dm,
        provider, agent_card_url, authentication_requirements, supported_interactions,
        source_url, last_verified_at, 0 AS is_primary
    FROM relationship_contacts
    UNION ALL
    SELECT 'human:' || person_id, cp.contact_id, contact_type, value,
        CASE WHEN verification_status IN ('invalid','stale') THEN 'unavailable' ELSE 'unknown' END,
        'unknown', NULL, NULL, NULL, NULL, s.url, last_verified_at, is_primary
    FROM contact_points cp LEFT JOIN sources s ON s.source_id=cp.source_id
    WHERE NOT EXISTS (SELECT 1 FROM relationship_contacts rc WHERE rc.legacy_contact_id=cp.contact_id)
), activity AS (
    SELECT profile_id, event_id, channel, kind, occurred_at, observed_at,
        target_is_ours=1 AS target_is_ours, target_url, interaction_url, external_reference,
        account_key, provider_id, 'relationship_events'::text AS source,
        CASE WHEN event_id LIKE 'xdm\_%' ESCAPE '\' OR event_id LIKE 'dm\_x\_%' ESCAPE '\' THEN 'dm' WHEN interaction_url IS NOT NULL OR kind IN ('like','comment') OR event_id LIKE 'x\_notification\_%' ESCAPE '\' THEN 'public' ELSE 'unknown' END AS surface
    FROM relationship_events
    UNION ALL
    SELECT 'human:' || person_id, event_id, 'x', interaction, occurred_at, created_at,
        target_status='registered' OR
        lower(split_part(regexp_replace(target_url,'^https?://(www\.)?(x|twitter)\.com/',''), '/', 1))=lower(ltrim(account,'@')),
        target_url, interaction_url, provider_id, account, provider_id, 'x_engagement_events', 'public'
    FROM x_engagement_events xe WHERE person_id IS NOT NULL AND identity_status='matched'
      AND NOT EXISTS (SELECT 1 FROM relationship_events re WHERE re.event_id=xe.event_id)
    UNION ALL
    SELECT 'human:' || oe.person_id, oe.event_id, oe.channel,
        CASE WHEN oe.direction='outbound' THEN 'outbound' ELSE 'reply' END,
        CASE WHEN oe.event_id LIKE 'xdm\_in\_%' ESCAPE '\' OR oe.event_id LIKE 'xdm\_out\_%' ESCAPE '\' THEN NULL ELSE oe.occurred_at END,
        CASE WHEN oe.event_id LIKE 'xdm\_in\_%' ESCAPE '\' OR oe.event_id LIKE 'xdm\_out\_%' ESCAPE '\' THEN oe.occurred_at ELSE oe.created_at END,
        true, NULL, NULL, oe.external_reference,
        to_jsonb(oe)->>'account_key', NULL, 'outreach_events',
        CASE WHEN oe.event_id LIKE 'xdm\_%' ESCAPE '\' OR oe.event_id LIKE 'dm\_x\_%' ESCAPE '\' THEN 'dm' ELSE 'unknown' END
    FROM outreach_events oe
    WHERE oe.person_id IS NOT NULL
      AND ((oe.direction='outbound' AND oe.outcome IN ('sent','delivered'))
           OR (oe.direction='inbound' AND oe.outcome IN ('replied','declined','opted_out')))
      AND NOT EXISTS (SELECT 1 FROM x_engagement_events xe WHERE xe.event_id=oe.event_id)
      AND NOT EXISTS (SELECT 1 FROM relationship_events re WHERE re.event_id=oe.event_id)
), timed_activity AS (
    SELECT *, CASE WHEN crm_relationship_timestamp(occurred_at)<=CURRENT_TIMESTAMP THEN crm_relationship_timestamp(occurred_at) END AS event_time,
        CASE WHEN crm_relationship_timestamp(observed_at)<=CURRENT_TIMESTAMP THEN crm_relationship_timestamp(observed_at) END AS observation_time
    FROM activity
), contact_entries AS (
    SELECT *, coalesce(event_time,observation_time) AS effective_at,
        CASE WHEN event_time IS NOT NULL THEN 'occurred' WHEN observation_time IS NOT NULL THEN 'observed' ELSE 'unknown' END AS time_basis,
        CASE WHEN kind='outbound' THEN 'outbound' ELSE 'inbound' END AS direction
    FROM timed_activity WHERE kind='outbound' OR (kind IN ('like','comment','reply') AND target_is_ours)
), contact_objects AS (
    SELECT *, jsonb_build_object(
        'event_id',event_id,'channel',channel,'kind',kind,'direction',direction,
        'occurred_at',event_time,'observed_at',observation_time,'effective_at',effective_at,'time_basis',time_basis,
        'account_key',account_key,'provider_id',provider_id,'surface',surface,'source',source,
        'target_url',target_url,'interaction_url',interaction_url,'external_reference',external_reference) AS entry,
        row_number() OVER (PARTITION BY profile_id,channel,kind,direction,account_key,surface
                           ORDER BY effective_at DESC NULLS LAST,event_id DESC) AS route_rank
    FROM contact_entries
), audience AS (
    SELECT person_id, min(tag_id) AS category FROM current_person_tags
    WHERE tag_id IN ('personal_agent_owners','assistant_developers','early_adopters')
    GROUP BY person_id HAVING count(*)=1
)
SELECT i.*, c.canonical_name AS company_name, coalesce(p.audience,a.category) AS audience,
    p.owner_person_id,
    coalesce(p.can_receive_requests,'unknown') AS can_receive_requests,
    coalesce(p.can_call_external_apis,'unknown') AS can_call_external_apis,
    coalesce(p.can_connect_mcp,'unknown') AS can_connect_mcp,
    coalesce(p.can_install_integrations,'unknown') AS can_install_integrations,
    coalesce(p.owner_approval_required,'unknown') AS owner_approval_required,
    channels.email, channels.x_url, channels.linkedin_url, channels.github_url,
    channels.reddit_url, channels.moltbook_url,
    coalesce(channels.dm,'[]'::jsonb) AS dm,
    coalesce(channels.contact_channels,ARRAY[]::text[]) AS contact_channels,
    coalesce(channels.contact_points,'[]'::jsonb) AS contact_points,
    recent.last_like_at, recent.last_comment_at, recent.last_reply_at, recent.last_outbound_contact_at,
    recent.last_like_observed_at, recent.last_comment_observed_at, recent.last_reply_observed_at,
    recent.last_installed_at, recent.last_mcp_connected_at, recent.first_search_at, recent.last_search_at,
    recent.last_repeat_usage_at, latest.event_time AS last_contact_at,
    latest.channel AS last_contact_channel, latest.kind AS last_contact_kind,
    latest.target_url AS last_contact_target_url, latest.interaction_url AS last_contact_interaction_url,
    coalesce(history.last_contact,'[]'::jsonb) AS last_contact,
    coalesce(history.contact_history,'[]'::jsonb) AS contact_history,
    history.latest_contact, history.last_contact_effective_at, history.last_contact_time_basis
FROM identities i
LEFT JOIN companies c ON c.company_id=i.company_id
LEFT JOIN relationship_profiles p ON p.profile_id=i.profile_id
LEFT JOIN audience a ON i.entity_type='human' AND a.person_id=i.entity_id
LEFT JOIN LATERAL (
    SELECT
        (array_agg(address ORDER BY availability='unavailable',is_primary DESC,contact_id) FILTER(WHERE channel IN ('email','agent_email') AND address<>''))[1] AS email,
        (array_agg(address ORDER BY availability='unavailable',is_primary DESC,contact_id) FILTER(WHERE channel='x' AND address<>''))[1] AS x_url,
        (array_agg(address ORDER BY availability='unavailable',is_primary DESC,contact_id) FILTER(WHERE channel='linkedin' AND address<>''))[1] AS linkedin_url,
        (array_agg(address ORDER BY availability='unavailable',is_primary DESC,contact_id) FILTER(WHERE channel='github' AND address<>''))[1] AS github_url,
        (array_agg(address ORDER BY availability='unavailable',is_primary DESC,contact_id) FILTER(WHERE channel='reddit' AND address<>''))[1] AS reddit_url,
        (array_agg(address ORDER BY availability='unavailable',is_primary DESC,contact_id) FILTER(WHERE channel='moltbook' AND address<>''))[1] AS moltbook_url,
        jsonb_agg(jsonb_build_object('channel',channel,'address',address,'availability',availability) ORDER BY channel,contact_id)
            FILTER(WHERE address<>'' AND (supports_dm='yes' OR channel IN ('agentdm','masumi','agentlist'))) AS dm,
        array_agg(DISTINCT channel ORDER BY channel) FILTER(WHERE address<>'') AS contact_channels,
        jsonb_agg(to_jsonb(ct)-'profile_id' ORDER BY channel,contact_id) AS contact_points
    FROM contacts ct WHERE ct.profile_id=i.profile_id
) channels ON true
LEFT JOIN LATERAL (
    SELECT max(event_time) FILTER(WHERE kind='like' AND target_is_ours AND event_time<=CURRENT_TIMESTAMP) AS last_like_at,
        max(event_time) FILTER(WHERE kind='comment' AND target_is_ours AND event_time<=CURRENT_TIMESTAMP) AS last_comment_at,
        max(event_time) FILTER(WHERE kind='reply' AND target_is_ours AND event_time<=CURRENT_TIMESTAMP) AS last_reply_at,
        max(event_time) FILTER(WHERE kind='outbound' AND event_time<=CURRENT_TIMESTAMP) AS last_outbound_contact_at,
        max(observation_time) FILTER(WHERE kind='like' AND target_is_ours) AS last_like_observed_at,
        max(observation_time) FILTER(WHERE kind='comment' AND target_is_ours) AS last_comment_observed_at,
        max(observation_time) FILTER(WHERE kind='reply' AND target_is_ours) AS last_reply_observed_at,
        max(event_time) FILTER(WHERE kind='installed') AS last_installed_at,
        max(event_time) FILTER(WHERE kind='mcp_connected') AS last_mcp_connected_at,
        min(event_time) FILTER(WHERE kind='search') AS first_search_at,
        max(event_time) FILTER(WHERE kind='search') AS last_search_at,
        max(event_time) FILTER(WHERE kind='repeat_usage') AS last_repeat_usage_at
    FROM timed_activity e WHERE e.profile_id=i.profile_id
) recent ON true
LEFT JOIN LATERAL (
    SELECT * FROM timed_activity e WHERE e.profile_id=i.profile_id AND event_time<=CURRENT_TIMESTAMP
        AND (kind='outbound' OR (kind IN ('like','comment','reply') AND target_is_ours))
    ORDER BY event_time DESC,event_id DESC LIMIT 1
) latest ON true
LEFT JOIN LATERAL (
    SELECT jsonb_agg(entry ORDER BY effective_at DESC NULLS LAST,event_id DESC) FILTER(WHERE route_rank=1) AS last_contact,
        jsonb_agg(entry ORDER BY effective_at DESC NULLS LAST,event_id DESC) AS contact_history,
        (jsonb_agg(entry ORDER BY effective_at DESC NULLS LAST,event_id DESC))->0 AS latest_contact,
        max(effective_at) AS last_contact_effective_at,
        (array_agg(time_basis ORDER BY effective_at DESC NULLS LAST,event_id DESC))[1] AS last_contact_time_basis
    FROM contact_objects o WHERE o.profile_id=i.profile_id
) history ON true;
