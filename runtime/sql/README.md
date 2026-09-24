# CRM schemas

schema.sql defines core facts; route_review_schema.sql, tags_schema.sql, copy_schema.sql and email_schema.sql add their corresponding structures. The shared database migration uses a privacy-filtered subset; private drafts and evidence remain local. Never apply these files blindly to an existing production database. Use the shared CRM setup and migration runbook.

Start with [setup](../../docs/SETUP.md).
