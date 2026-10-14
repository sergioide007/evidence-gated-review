# Reliability checklist
- Every outbound call has a timeout. Retries are bounded, jittered, and only for idempotent or keyed operations.
- Non-idempotent writes (POST/charge/send) carry an idempotency key; duplicates are handled server-side.
- Circuit breaker / fail-fast around slow dependencies; behavior defined when open.
- Loops and queues are bounded (max iterations, max depth, max queue size, backpressure).
- Partial failure is a designed state: what does the caller see? what is persisted?
- Timeouts compose: caller timeout > sum of downstream timeouts + retries? (retry amplification)
- Graceful degradation path exists and is tested.
