# Apollo contact lookup

Explicit contact lookup requires a source-backed full name and company. The manual adapter is
`runtime/crm/apollo_discovery.py`; broad keyword collection remains disabled.

## Run

```sh
PYTHONPATH=runtime python3 -B -m crm.apollo_discovery \
  --name 'Full Name' --company 'Company' --company-domain example.org \
  --source-url https://example.org/team --output /private/tmp/contact-result.json
```

Authentication uses `APOLLO_API_KEY`, `APOLLO_API_TOKEN`, or the existing local
secret loader (`--credentials-dir` can point to an authorized credential folder).
Supply the existing authorized credential privately; a fresh checkout contains no credentials.

An input JSON via `--input` may instead supply source-backed `identity_key`,
`source_url`, and full `name`/`company` or an exact `linkedin_url`. The source must
establish identity. Company domain is optional and, when supplied, must match too.
The adapter accepts exact normalized names and companies (standard legal suffixes
are ignored), rejects low/none confidence and mismatched profiles, and exposes an
email only when a real nonmasked address has provider `email_status=verified`.
Contact match alone and search `has_email` flags do not establish an email route.

One invocation makes at most one native `/api/v1/people/match` request, which may
consume provider credits. Personal email, phone reveal and waterfall are disabled.
Results are written mode 0600; replaying the same candidate/output does not call
Apollo again. A different candidate at that output is rejected. An interrupted
attempt remains uncertain and requires readback before another provider request.
Never commit credentials or private result evidence.

Ops owns identity resolution, suppression checks, CRM and PostHog. Supplying agents own recipient copy; Email owns delivery. This adapter performs no outreach or enrollment.

Fixture coverage includes identity mismatches, incomplete inputs, email status,
provider errors, one-request behavior, private output permissions, and replay and
collision protection. Run:

```sh
PYTHONPATH=runtime python3 -B -m unittest discover -s runtime/tests -p 'test_apollo*.py'
```

[People Enrichment API](https://docs.apollo.io/reference/people-enrichment)
documents the identity and business-contact lookup endpoint.
