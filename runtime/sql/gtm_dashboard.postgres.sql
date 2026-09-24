-- Additive CRM evidence store and live dashboard views. No existing tables are changed.
CREATE TABLE IF NOT EXISTS crm_gtm.dashboard_evidence_imports (
    import_id text PRIMARY KEY,
    source_account text NOT NULL,
    observed_at timestamptz NOT NULL,
    source_sha256 text NOT NULL,
    coverage text NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS crm_gtm.dashboard_message_evidence (
    import_id text NOT NULL REFERENCES crm_gtm.dashboard_evidence_imports(import_id),
    record_id text NOT NULL,
    area text NOT NULL CHECK (area IN ('replies','dms')),
    channel text NOT NULL,
    account text NOT NULL,
    direction text NOT NULL CHECK (direction IN ('inbound','outbound')),
    provider_id text,
    provider_id_status text NOT NULL,
    occurred_at timestamptz NOT NULL,
    timestamp_source text NOT NULL,
    body text,
    body_status text NOT NULL,
    counterparty_handle text,
    person_id text REFERENCES crm_gtm.people(person_id),
    conversation_id text,
    url text,
    in_reply_to_url text,
    PRIMARY KEY (import_id, record_id)
);
CREATE TABLE IF NOT EXISTS crm_gtm.dashboard_message_backfill (
    record_id text PRIMARY KEY,
    source_path text NOT NULL,
    source_sha256 text NOT NULL,
    channel text NOT NULL,
    account text NOT NULL,
    direction text NOT NULL CHECK (direction IN ('inbound','outbound')),
    message_kind text NOT NULL,
    provider_id text,
    occurred_at timestamptz NOT NULL,
    body text NOT NULL,
    body_status text NOT NULL,
    evidence_status text NOT NULL,
    counterparty text,
    person_id text,
    conversation_id text,
    evidence_url text,
    imported_at timestamptz NOT NULL DEFAULT now()
);
CREATE OR REPLACE VIEW crm_gtm.v_gtm_dashboard_messages AS
WITH latest AS (
 SELECT DISTINCT ON (source_account) import_id,source_account,observed_at,coverage
 FROM crm_gtm.dashboard_evidence_imports
 ORDER BY source_account,observed_at DESC,imported_at DESC,import_id DESC
)
SELECT m.*, i.observed_at AS audited_at, i.coverage,
 CASE WHEN m.person_id IS NULL THEN 'Not linked to CRM'
      ELSE COALESCE(NULLIF(replace(p.audience,'_',' '),''), t.audiences, 'Unclassified in CRM') END AS category
FROM crm_gtm.dashboard_message_evidence m JOIN latest i USING(import_id)
LEFT JOIN crm_gtm.relationship_profiles p ON p.person_id=m.person_id
LEFT JOIN (
 SELECT person_id,string_agg(replace(tag_id,'_',' '), ', ' ORDER BY tag_id) AS audiences
 FROM crm_gtm.current_person_tags WHERE kind='audience' GROUP BY person_id
) t ON t.person_id=m.person_id;
CREATE OR REPLACE VIEW crm_gtm.v_gtm_dashboard_responses AS
SELECT o.import_id,o.area,o.channel,o.account,o.record_id AS outbound_record_id,
       r.record_id AS inbound_record_id, o.body AS what_we_sent,r.body AS what_they_sent_back,
       r.counterparty_handle,r.category,r.person_id,r.provider_id_status AS response_evidence,
       o.provider_id_status AS outbound_evidence,o.occurred_at AS sent_at,r.occurred_at AS response_at,
       r.url AS evidence_url,o.conversation_id,o.audited_at
FROM crm_gtm.v_gtm_dashboard_messages o JOIN crm_gtm.v_gtm_dashboard_messages r
 ON r.import_id=o.import_id AND r.channel=o.channel AND r.account=o.account AND r.area=o.area
 AND r.direction='inbound'
 AND ((o.area='replies' AND r.in_reply_to_url=o.url)
      OR (o.area='dms' AND r.conversation_id=o.conversation_id))
WHERE o.direction='outbound' AND o.body IS NOT NULL AND r.body IS NOT NULL
      AND r.occurred_at>=o.occurred_at;
CREATE OR REPLACE VIEW crm_gtm.v_gtm_dashboard_summary AS
SELECT m.area,m.channel,m.account,max(m.audited_at) AS audited_at,
 count(*) FILTER (WHERE m.direction='outbound') AS outbound_messages,
 count(DISTINCT m.conversation_id) FILTER (WHERE m.direction='outbound') AS conversations,
 CASE WHEN m.area='replies' THEN count(DISTINCT m.record_id) FILTER (WHERE m.direction='outbound' AND EXISTS (
   SELECT 1 FROM crm_gtm.v_gtm_dashboard_responses r WHERE r.import_id=m.import_id AND r.outbound_record_id=m.record_id))
 ELSE count(DISTINCT m.conversation_id) FILTER (WHERE m.direction='outbound' AND EXISTS (
   SELECT 1 FROM crm_gtm.v_gtm_dashboard_responses r WHERE r.import_id=m.import_id AND r.conversation_id=m.conversation_id)) END AS observed_successes,
 CASE WHEN m.area='replies' THEN count(DISTINCT m.record_id) FILTER (WHERE m.direction='outbound' AND EXISTS (
   SELECT 1 FROM crm_gtm.v_gtm_dashboard_responses r WHERE r.import_id=m.import_id AND r.outbound_record_id=m.record_id AND r.response_evidence='confirmed'))
 ELSE count(DISTINCT m.conversation_id) FILTER (WHERE m.direction='outbound' AND EXISTS (
   SELECT 1 FROM crm_gtm.v_gtm_dashboard_responses r WHERE r.import_id=m.import_id AND r.conversation_id=m.conversation_id AND r.response_evidence='confirmed')) END AS confirmed_successes,
 count(*) FILTER (WHERE m.direction='inbound' AND m.provider_id_status='pending_readback') AS pending_provider_ids,
 max(m.coverage) AS coverage
FROM crm_gtm.v_gtm_dashboard_messages m GROUP BY m.area,m.channel,m.account;
CREATE OR REPLACE VIEW crm_gtm.v_gtm_dashboard_categories AS
SELECT area,channel,account,category,
       count(DISTINCT counterparty_handle) AS responding_accounts,
       count(DISTINCT counterparty_handle) FILTER (WHERE response_evidence='confirmed') AS confirmed_accounts,
       count(DISTINCT counterparty_handle) FILTER (WHERE response_evidence='pending_readback') AS pending_accounts,
       max(audited_at) AS audited_at
FROM crm_gtm.v_gtm_dashboard_responses GROUP BY area,channel,account,category;

-- Complete row-level CRM communication history for the dashboard. The bounded
-- evidence import wins when it overlaps an outreach row because it can carry
-- verified message text and provider evidence. Exact overlaps are removed by
-- stable DM event ID, or by the unique channel/direction/person/timestamp tuple.
-- Receipt-only rows remain visible with missing text stated explicitly.
CREATE OR REPLACE VIEW crm_gtm.v_gtm_dashboard_history AS
WITH imported_evidence AS (
    SELECT
        m.occurred_at,
        m.channel,
        m.direction,
        CASE WHEN m.area = 'replies' THEN 'public_reply' ELSE 'dm' END AS message_kind,
        m.account,
        COALESCE(NULLIF(m.counterparty_handle, ''), p.full_name, 'Unknown counterparty') AS counterparty,
        m.person_id,
        COALESCE(m.body, '[Message text unavailable in captured evidence]') AS message_text,
        m.body_status,
        m.provider_id_status AS evidence_status,
        m.provider_id,
        m.conversation_id,
        m.url AS evidence_url,
        'dashboard_message_evidence'::text AS crm_sources,
        'canonical evidence row'::text AS dedup_status,
        m.record_id AS source_record_id
    FROM crm_gtm.v_gtm_dashboard_messages m
    LEFT JOIN crm_gtm.people p ON p.person_id = m.person_id
), backfill_ranked AS (
    SELECT
        b.occurred_at,
        b.channel,
        b.direction,
        b.message_kind,
        b.account,
        COALESCE(NULLIF(b.counterparty, ''), p.full_name, 'Unknown counterparty') AS counterparty,
        b.person_id,
        b.body AS message_text,
        b.body_status,
        b.evidence_status,
        b.provider_id,
        b.conversation_id,
        b.evidence_url,
        'dashboard_message_backfill'::text AS crm_sources,
        'verified local/provider backfill'::text AS dedup_status,
        b.record_id AS source_record_id,
        row_number() OVER (
            PARTITION BY b.channel, b.direction,
                COALESCE(NULLIF(b.provider_id, ''), encode(sha256((b.body || '|' || b.occurred_at::text)::bytea), 'hex'))
            ORDER BY b.source_path, b.record_id
        ) AS source_rank
    FROM crm_gtm.dashboard_message_backfill b
    LEFT JOIN crm_gtm.people p ON p.person_id = b.person_id
), backfill AS (
    SELECT occurred_at,channel,direction,message_kind,account,counterparty,person_id,
           message_text,body_status,evidence_status,provider_id,conversation_id,
           evidence_url,crm_sources,dedup_status,source_record_id
    FROM backfill_ranked b
    WHERE source_rank = 1
      AND NOT EXISTS (
          SELECT 1 FROM imported_evidence e
          WHERE e.channel=b.channel AND e.direction=b.direction
            AND ((e.provider_id IS NOT NULL AND e.provider_id=b.provider_id)
              OR (e.person_id IS NOT NULL AND e.person_id=b.person_id AND e.occurred_at=b.occurred_at AND e.message_text=b.message_text))
      )
), evidence AS (
    SELECT * FROM imported_evidence
    UNION ALL
    SELECT * FROM backfill
), outreach AS (
    SELECT
        NULLIF(o.occurred_at, '')::timestamptz AS occurred_at,
        o.channel,
        o.direction,
        COALESCE(NULLIF(o.message_kind, ''), 'unspecified_message') AS message_kind,
        COALESCE(NULLIF(o.account_key, ''), 'Unknown account') AS account,
        COALESCE(p.full_name, 'Unknown counterparty') AS counterparty,
        o.person_id,
        '[Message text unavailable in outreach_events]'::text AS message_text,
        'not_captured'::text AS body_status,
        CASE WHEN NULLIF(o.external_reference, '') IS NULL
             THEN 'CRM event only; provider reference unavailable'
             ELSE 'CRM event with external reference' END AS evidence_status,
        NULLIF(o.external_reference, '') AS provider_id,
        NULL::text AS conversation_id,
        NULLIF(o.external_reference, '') AS evidence_url,
        'outreach_events'::text AS crm_sources,
        'unique after exact evidence-overlap removal'::text AS dedup_status,
        o.event_id AS source_record_id
    FROM crm_gtm.outreach_events o
    LEFT JOIN crm_gtm.people p ON p.person_id = o.person_id
    WHERE NULLIF(o.occurred_at, '') IS NOT NULL
      AND NOT EXISTS (
          SELECT 1
          FROM evidence e
          WHERE o.event_id = 'dm_x_' || split_part(e.source_record_id, ':', 3)
             OR (
                 e.channel = o.channel
                 AND e.direction = o.direction
                 AND e.person_id = o.person_id
                 AND e.occurred_at = NULLIF(o.occurred_at, '')::timestamptz
             )
      )
), receipts AS (
    SELECT
        r.occurred_at,
        'x'::text AS channel,
        r.direction,
        'dm_receipt'::text AS message_kind,
        'Hashed account key'::text AS account,
        'Hashed counterparty key'::text AS counterparty,
        NULL::text AS person_id,
        '[Message text unavailable in x_dm_receipts]'::text AS message_text,
        'not_captured'::text AS body_status,
        'Browser-verified receipt; identity and conversation keys are hashed'::text AS evidence_status,
        r.provider_message_id AS provider_id,
        r.conversation_key AS conversation_id,
        NULL::text AS evidence_url,
        'x_dm_receipts'::text AS crm_sources,
        'receipt-only row; no exact provider-ID overlap found'::text AS dedup_status,
        r.receipt_id AS source_record_id
    FROM crm_gtm.x_dm_receipts r
)
SELECT * FROM evidence
UNION ALL
SELECT * FROM outreach
UNION ALL
SELECT * FROM receipts;
