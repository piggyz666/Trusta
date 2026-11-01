# TRUSTA V1 — Codex Implementation Brief

Implement TRUSTA V1 exactly according to `TRUSTA_V1_Engineering_Spec.md`.

## Goal
Build one complete vertical slice:
“给张三转 2000 元”
-> Supervisor
-> Planner
-> Transaction Agent
-> BankActionIR
-> deterministic trusted execution
-> Virtual Bank transfer
-> hard verification
-> execution receipt
-> tamper-evident audit.

## Frozen boundaries
1. LLM agents may READ / ANALYZE / PROPOSE only.
2. No LLM agent may directly execute a financial write.
3. Every write passes Schema Validator -> Capability -> Policy -> Authorization -> Execution Contract -> Tool Gateway -> Bank Adapter.
4. Tool Gateway is the only path to the bank adapter.
5. Authorization binds to the exact action hash.
6. The same idempotency key never debits twice.
7. Post-execution verification re-reads bank state.
8. Uncertain post-write verification routes to MANUAL_REVIEW; never auto-retry.
9. Blackboard is authoritative shared state.
10. Scheduler transitions are deterministic, not LLM-controlled.

## Scope
Functional agents:
- Supervisor
- Planner
- Transaction Agent

Skeleton registration only:
- Finance Agent
- Service Agent
- Wealth Agent

Implement:
- API/channel layer
- Multi-Agent runtime
- Blackboard
- Message Bus
- deterministic Scheduler
- BankActionIR schema
- Schema Validator
- Capability Manager
- Policy Engine
- Authorization Engine
- Execution Contract service
- Tool Gateway
- VirtualBankAdapter
- persistent Virtual Bank
- Hard Verifier
- Execution Receipt
- append-only hash-chain audit
- signed critical agent messages and replay protection
- minimal frontend
- automated tests
- seed data
- local startup and documentation

## Acceptance
Implement and pass all 20 tests listed in the engineering specification before declaring V1 complete.

Do not add full card, subscription, wealth, blockchain, ZKP, native mobile or IM integrations in V1.
Do not bypass or simplify frozen safety boundaries.
Do not expand scope without explicit approval.

Before completion:
- run the entire test suite;
- run the actual end-to-end transfer flow;
- verify no financial write bypasses Tool Gateway;
- verify no test depends on an always-success bank mock;
- remove dead experimental code;
- document assumptions and material limitations.
