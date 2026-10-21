# TRUSTA V1 Engineering Specification

## 1. V1 Objective
TRUSTA V1 must prove one complete, secure and auditable banking workflow:

Natural-language transfer request -> Multi-Agent understanding/planning -> structured bank action -> deterministic safety control -> user authorization -> banking execution -> post-execution verification -> audit receipt.

Canonical demo request: “给张三转 2000 元。”

V1 is complete only when the transfer changes the Virtual Bank's persistent state and the system can prove that the executed action matches the user-authorized action.

## 2. Non-goals
V1 does not implement all six competition scenarios, production core banking, native apps, real-bank production APIs, IM integrations, large-scale RAG, ZKP, blockchain in the transaction critical path, many agents, or LLM-decided hard authorization rules.

## 3. Frozen Principles
1. Separation of Reasoning and Execution: LLM agents may READ / ANALYZE / PROPOSE, but never hold final financial EXECUTE authority.
2. Blackboard-centered Multi-Agent Runtime: Supervisor + Planner + Blackboard + Message Bus + deterministic Runtime/Scheduler.
3. Adapter-based banking integration: TRUSTA Core is bank-agnostic; V1 uses VirtualBankAdapter.
4. Execute -> Verify: every state-changing action is verified against authoritative bank state after execution.
5. Fail Closed: schema, capability, policy, authorization, contract, signature, replay or verification failure stops unsafe progression.

## 4. V1 Runtime Architecture
User
  -> Channel Gateway
  -> Supervisor Agent
  -> Planner Agent
  -> Transaction Agent
  -> Task DAG + Blackboard + Message Bus
  -> Trusted Execution Boundary
  -> BankActionIR
  -> Schema Validator
  -> Capability Manager
  -> Policy Engine
  -> Authorization Engine
  -> Execution Contract
  -> Tool Gateway
  -> VirtualBankAdapter
  -> Virtual Bank
  -> Hard Verifier
  -> Execution Receipt
  -> Audit Engine

## 5. V1 Agents
### Supervisor Agent
Receives normalized user requests, identifies task family, creates/loads tasks, routes to Planner, requests clarification for critical ambiguity, and reacts to completion/failure events.
Must not decide final policy, approve authorization, call state-changing bank tools, mutate the ledger or fabricate audit proofs.

### Planner Agent
Converts the user goal into a structured Task DAG with dependencies, logical owners and steps that may require financial actions.
Must not execute bank actions.

### Transaction Agent
Resolves transfer semantics, identifies required account/payee/amount/currency fields, uses permitted read capabilities, creates a transfer proposal and generates BankActionIR.
Must not call transfer.execute.

### Registered but inactive in V1
Finance Agent, Service Agent, Wealth Agent.

## 6. Canonical Task DAG
T01 Parse User Goal
-> T02 Resolve Recipient
-> T03 Resolve Source Account
-> T04 Check Available Balance
-> T05 Build BankActionIR
-> T06 Policy Evaluation
-> T07 User Authorization
-> T08 Execute Transfer
-> T09 Verify Result
-> T10 Generate Receipt

Required step states:
PENDING, READY, RUNNING, WAITING_USER, COMPLETED, FAILED, BLOCKED, MANUAL_REVIEW, CANCELLED.

The deterministic Runtime/Scheduler controls legal state transitions.

## 7. Blackboard
The Blackboard is the authoritative shared task-state store.

Minimum logical structure:
- task_id
- user_id
- user_goal
- task_type
- status
- context: source_account, recipient, balance
- dag
- steps
- agent_results
- proposed_actions
- policy_decision
- authorization
- execution
- verification
- receipt
- audit_refs
- errors

Rules:
- Agents do not maintain private copies of critical authoritative state.
- Large outputs live in Blackboard.
- Message Bus events contain references rather than duplicated long-form state.
- Material updates are attributable to actor and timestamp.

## 8. Message Bus
V1 may use an in-process bus, but the interface must remain implementation-independent.

Required events:
TASK_CREATED
STEP_READY
STEP_STARTED
STEP_COMPLETED
STEP_FAILED
ACTION_PROPOSED
AUTHORIZATION_REQUIRED
AUTHORIZATION_GRANTED
AUTHORIZATION_DENIED
AUTHORIZATION_EXPIRED
EXECUTION_STARTED
EXECUTION_COMPLETED
EXECUTION_FAILED
VERIFICATION_COMPLETED
VERIFICATION_FAILED
MANUAL_REVIEW_REQUIRED

Minimum envelope:
event_id, event_type, task_id, step_id, sender, receiver, timestamp, payload_ref, payload_hash.
Trusted-message extension: nonce, signature.

## 9. BankActionIR
BankActionIR is the canonical boundary object between agentic reasoning and trusted execution.

V1 transfer fields:
action_id
task_id
action_type = TRANSFER
initiating_agent
customer_id
source_account_id
recipient_id
amount
currency
requested_capability = banking.transfer
reason
evidence_refs
preconditions
expected_postconditions
created_at
expires_at
idempotency_key

Critical rule: account/payee identifiers and bank facts must originate from authoritative banking data, not invented LLM output.

## 10. Trusted Execution Pipeline
Every state-changing action must pass:

BankActionIR
-> Schema Validation
-> Capability Check
-> Policy
-> Authorization
-> Execution Contract
-> Tool Gateway
-> Bank Adapter
-> Bank
-> Hard Verification
-> Receipt
-> Audit

No stage may be bypassed.

## 11. Schema Validation
Validate at minimum:
- legal action type
- amount > 0
- supported currency
- valid source account
- valid recipient
- valid capability
- non-expired action
- non-empty idempotency key
- well-formed expected postconditions

Failure => REJECTED_SCHEMA.

## 12. Capability Manager
Example Transaction Agent permissions:

Allowed:
account.read
balance.read
payee.read
transfer.propose

Denied:
transfer.execute
card.freeze
subscription.cancel
wealth.purchase

Capability enforcement is deterministic and independent of the LLM.

## 13. Policy Engine
Deterministic and configurable.

Minimum rules:
- insufficient balance -> BLOCK
- frozen source account -> BLOCK
- new recipient -> at least CONFIRM
- transfer amount >= sandbox high-risk threshold -> STRONG_AUTH

Decisions:
ALLOW, CONFIRM, STRONG_AUTH, BLOCK

Output:
policy_decision_id, decision, rule_ids, reason_codes, evaluated_at.

Thresholds belong to bank policy configuration, not prompts.

## 14. Authorization Engine
States:
NOT_REQUIRED
PENDING_CONFIRMATION
PENDING_STRONG_AUTH
AUTHORIZED
DENIED
EXPIRED

Authorization record:
authorization_id
user_id
action_hash
auth_method
status
created_at
expires_at

Authorization must bind to the exact BankActionIR hash. Changing protected parameters invalidates it.

## 15. Execution Contract
Only a valid Execution Contract can authorize a state-changing Tool Gateway request.

Fields:
contract_id
action_id
approved_action_hash
policy_decision_id
authorization_id
capability
preconditions
issued_at
expires_at
idempotency_key
contract_hash

Reject expired contracts, action-hash mismatch, invalid authorization, missing capability and unsafe repeated execution.

## 16. Tool Gateway
Canonical V1 capabilities:
account.get
account.get_balance
payee.resolve
transfer.create
transaction.get

Rules:
- all bank calls pass through Tool Gateway
- write calls require a valid Execution Contract
- read calls require capability authorization
- agents never call VirtualBankAdapter directly

## 17. Bank Adapter
V1 implements VirtualBankAdapter with:
get_account
get_balance
resolve_payee
create_transfer
get_transaction

Future real-bank adapters map the same canonical capabilities to their own APIs.

## 18. Bank Capability Manifest
Each adapter exposes supported canonical capabilities and may declare:
input_schema
output_schema
risk_class
requires_auth
reversible
supports_idempotency

TRUSTA Core consumes the manifest rather than hardcoding bank-specific APIs.

## 19. Virtual Bank Sandbox
Must be stateful and persistent.

Required entities:
Customer
Account
Payee
Transfer
Transaction
LedgerEntry
AuthorizationRecord

Transfers should use a simplified balanced ledger:
User Account: DEBIT 2000
Settlement Account: CREDIT 2000

Invariant: sum(debits) == sum(credits).

Do not implement transfer as only balance -= amount.

## 20. Transfer State Machine
CREATED -> AUTHORIZED -> PROCESSING -> SETTLED
Failure: FAILED
Reserved for later: REVERSED

## 21. Idempotency
The same idempotency_key flows through:
BankActionIR -> Execution Contract -> Tool Gateway -> Bank Adapter -> Transfer Record.

Repeated execution with the same valid key returns the existing transaction and must not debit twice.

## 22. Hard Verification
Verification re-reads authoritative bank state.

For a transfer verify:
expected_after_balance == actual_after_balance
actual_transaction.amount == approved_amount
actual_transaction.recipient_id == approved_recipient
actual_transaction.source_account_id == approved_source
actual_transaction.status == SETTLED

Outcomes:
VERIFIED
FAILED
MANUAL_REVIEW

If execution may already have happened but verification is uncertain, do not auto-retry.

## 23. Execution Receipt
Fields:
receipt_id
task_id
action_id
bank_transaction_id
approved_action_hash
policy_decision_id
authorization_id
pre_state_hash
post_state_hash
verification_status
executed_at
audit_ref

## 24. Audit Engine
V1 implements append-only event log + hash chain.

Each audit event:
audit_event_id
task_id
action_id
event_type
actor
timestamp
payload_hash
previous_hash
event_hash

verify_chain() must detect historical tampering.

Merkle aggregation and blockchain anchoring are later extensions. No PII is written to a future public chain.

## 25. Trusted Agent Messaging
Critical agent messages may contain:
message_id
task_id
step_id
sender_agent
receiver_agent
message_type
payload_ref
payload_hash
timestamp
nonce
signature

V1 supports signature verification, payload tamper detection and nonce-based replay detection.

## 26. Failure Semantics
Recipient not resolved -> WAITING_USER or FAILED
Insufficient balance -> BLOCKED
User denies action -> CANCELLED
Authorization expires -> WAITING_USER
Bank execution fails before settlement -> FAILED
Execution may have succeeded but verification fails -> MANUAL_REVIEW
Replayed message -> BLOCKED
Invalid signature -> BLOCKED
Capability violation -> BLOCKED

## 27. V1 UI
Functional only:
1. Chat / command input
2. Current Task / DAG status
3. Authorization Card
4. Transfer Result
5. Trust Trace

Trust Trace may show Intent, Plan, BankActionIR, Policy, Authorization, Execution, Verification, Receipt and Audit status.
Never expose private chain-of-thought.

## 28. Persistence
Persist at minimum:
customers
accounts
payees
transfers
transactions
ledger entries
tasks
task steps
BankActionIR
policy decisions
authorizations
execution contracts
receipts
audit events
processed idempotency keys
replay-protection nonces

A restart must not erase a WAITING_USER task.

## 29. V1 Acceptance Tests
Functional:
1. valid transfer reaches SETTLED
2. final balance correct
3. ledger balanced
4. transaction matches approved action
5. receipt generated
6. audit chain valid

Security:
7. amount tampering blocked
8. recipient tampering blocked
9. missing authorization blocked
10. capability bypass blocked
11. invalid signature blocked
12. payload tampering blocked
13. replay nonce blocked
14. duplicate idempotency causes no duplicate debit
15. insufficient balance blocked
16. frozen source account blocked

Reliability:
17. WAITING_USER survives restart
18. authorization expiry enforced
19. bank failure cannot produce false success
20. uncertain verification routes to MANUAL_REVIEW

V1 is not complete until all 20 pass.

## 30. Required Deliverables from Codex
- backend service
- functional frontend shell
- persistent Virtual Bank
- Multi-Agent runtime
- Blackboard
- Event Bus
- deterministic Scheduler
- trusted execution pipeline
- audit hash chain
- automated tests
- seed/demo data
- one-command local startup
- .env.example
- README
- architecture notes
- API docs

## 31. Codex Constraints
Codex must not:
- bypass Tool Gateway
- give an LLM execute permissions
- collapse Policy into prompts
- hardcode always-success banking behavior
- use only in-memory demo state
- treat HTTP success as sufficient verification
- auto-retry uncertain financial writes
- create unnecessary agents
- implement blockchain/ZKP before V1 acceptance
- expand scope without approval

## 32. Definition of Done
The scenario “给张三转 2000 元” must complete:

User Request
-> Supervisor
-> Planner
-> Transaction Agent
-> Task DAG
-> BankActionIR
-> Policy
-> Authorization
-> Execution Contract
-> Tool Gateway
-> Virtual Bank
-> Transfer SETTLED
-> Hard Verification PASS
-> Execution Receipt
-> Audit Chain VALID

And these attacks must fail:
amount tampering
recipient tampering
unauthorized execution
capability bypass
replay
signature forgery
duplicate transfer
