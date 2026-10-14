# State and data checklist
- Who owns each piece of state? Single writer or explicit concurrency control?
- What survives a restart? What is lost? Is loss acceptable?
- Ordering and duplicate delivery: are handlers idempotent? are events versioned?
- Eventual consistency: what is the staleness bound and who is exposed to it?
- Context serialization between components (e.g. MCP payloads): size limits, schema validation, versioning.
- Migrations: forward/backward compatibility during rolling deploys.
