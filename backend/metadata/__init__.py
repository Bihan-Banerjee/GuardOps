"""
backend/metadata — Phase 12 scan-metadata persistence.

GuardOps scans used to be ephemeral: a ConsolidatedReport was written to
security/reports/ as JSON/HTML and then forgotten. This package gives scans a
memory — a persistent store of every scan run and its findings — so the CLI can
answer "what changed since last build?", "is our posture improving?", and feed
the future guardops.live dashboard.

The storage backend sits behind the MetadataStore abstraction (base.py) so the
default SQLite implementation (sqlite_store.py) can be swapped for Postgres later
without touching any CLI command. Use get_store() / persist_report_safe() from
factory.py — never instantiate a store directly in command code.
"""
