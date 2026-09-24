# CRM synthetic tests

Run make source-check from the project root. Tests use isolated fixtures for routing, copy, intake, daily workflows, activity, analytics and recovery. PostgreSQL cases require an explicitly configured disposable loopback database and otherwise report skips. Fixture outcomes do not prove live delivery, browser coverage or production conversions.

Start with [setup](../../docs/SETUP.md).
