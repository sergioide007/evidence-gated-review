# Security checklist
- Trust boundaries: which inputs are untrusted (users, retrieved documents, repository content, tool output)?
- Indirect prompt injection: can untrusted content reach a model that can call tools or exfiltrate data?
- "Lethal trifecta" (private data + untrusted content + external communication): is at least one leg removed?
- Tool authorization per tool AND per operation; arguments validated against a schema; allowlists, not denylists.
- Secrets: never in prompts/logs; short-lived credentials; no write/push tokens in review contexts.
- Supply chain: pinned versions/hashes for skills, MCP servers, dependencies; review of tool descriptions.
- Authz is enforced outside the model; a model's refusal is not an access control.
