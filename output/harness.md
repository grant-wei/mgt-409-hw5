# Campus Customs database

The original database is `data/campus_customs.db`. The working copy is `data/campus_customs_new.db`. Both have the same initial SHA-256 hash: `23686A90D698F7FA6F901A3F0A9774DB9CBB31E13151C9E5717275F3321F360A`.

## Tables

| Table | Fields | Why it matters for the agents |
|---|---|---|
| `cash_accounts` | `name` TEXT (PK), `balance` REAL NOT NULL, `date` TEXT NOT NULL | Accounting reads the available cash before preparing an approved payment or purchase. |
| `desk` | `date_today` TEXT NOT NULL, `notes` TEXT | Supplies the shop's operating date for deciding whether rent and invoices are due or overdue. |
| `inventory` | `sku` TEXT (composite PK) NOT NULL, `name` TEXT NOT NULL, `size` TEXT (composite PK) NOT NULL, `qty` INTEGER NOT NULL, `location` TEXT NOT NULL | Inventory checks stock and size availability for customer orders and bulk requests. |
| `invoices` | `id` INTEGER (PK), `vendor_id` INTEGER NOT NULL, `amount` REAL NOT NULL, `due_date` TEXT NOT NULL, `status` TEXT NOT NULL, `description` TEXT | Accounting checks invoice status and amount. Vendor identity comes from `vendor_id`. |
| `leases` | `id` INTEGER (PK), `space_name` TEXT NOT NULL, `landlord` TEXT NOT NULL, `monthly_rent` REAL NOT NULL, `next_due` TEXT NOT NULL, `notes` TEXT | Facilities checks the shop's lease and rent timing. |
| `payments` | `id` INTEGER (PK), `kind` TEXT NOT NULL, `ref_id` INTEGER, `amount` REAL NOT NULL, `account` TEXT NOT NULL, `paid_at` TEXT NOT NULL, `approved_by` TEXT NOT NULL | Records approved cash outflows and their linked invoice or other reference. |
| `pricing` | `sku` TEXT (PK), `unit_cost` REAL NOT NULL, `list_price` REAL NOT NULL | Accounting evaluates margins and discount requests against product costs and list prices. |
| `purchases` | `id` INTEGER (PK), `ticket_id` INTEGER FK, `vendor_id` INTEGER FK, `sku` TEXT, `size` TEXT, `quantity` INTEGER, `quoted_unit_cost` REAL, `total_amount` REAL, `supplier_quote_reference` TEXT, `account` TEXT FK, `status` TEXT, `ordered_at` TEXT, `approved_by` TEXT, `approval_reference` TEXT | Records a human-approved purchase at an explicitly verified supplier quote as ordered and paid without treating it as delivered inventory. |
| `rent_payments` | `id` INTEGER (PK), `lease_id` INTEGER FK, `due_date` TEXT, `amount` REAL, `account` TEXT FK, `paid_at` TEXT, `approved_by` TEXT, `approval_reference` TEXT, UNIQUE (`lease_id`, `due_date`) | Records one approved payment per lease due date and prevents paying the same rent period twice. |
| `inventory_receipts` | `id` INTEGER (PK), `sku`, `size`, `received_quantity`, `receipt_date`, `previous_stock`, `resulting_stock`, `source_label`, `receipt_reference` UNIQUE, `approved_by`, `approval_reference` UNIQUE, UNIQUE SKU/size/quantity/date/source tuple | Durable, exactly approved simulation receipts, distinct from vendor evidence, orders, cash, and ticket outcomes. Created on the first successful receipt. |
| `tickets` | `id` INTEGER (PK), `type` TEXT NOT NULL, `requester` TEXT NOT NULL, `subject` TEXT NOT NULL, `sku` TEXT, `size` TEXT, `qty` INTEGER, `lease_id` INTEGER, `invoice_id` INTEGER, `status` TEXT NOT NULL, `notes` TEXT, `created_at` TEXT NOT NULL | Boss routes each request to the relevant specialist. Ticket references connect rent notices and invoice-related work to their records. |
| `vendors` | `id` INTEGER (PK), `name` TEXT NOT NULL, `specialty` TEXT NOT NULL, `lead_days` INTEGER NOT NULL | Inventory and Accounting use vendor capabilities and lead times when considering restock and purchasing. |

## Ticket relationships

- **101 — Bulldog tee:** `tickets.sku` + `tickets.size` (`CC-TEE-WHITE`, `S`) match `inventory.sku` + `inventory.size`. That stock row has quantity 0. Its `invoice_id` 501 links to `invoices.id` 501, a $840 open invoice due 2026-08-28. Invoice vendor 1 links to `vendors.id` 1, Bulldog Print Co. `pricing.sku` also matches the tee SKU, with unit cost $8 and list price $28. The SKU/size and pricing matches are field-value joins, not declared foreign keys.
- **102 — Rent due:** `tickets.lease_id` 1 links to `leases.id` 1, the Chapel Street shop lease with Elm City Properties, monthly rent $2,400, next due 2026-09-02. This is an explicit foreign key.
- **103 — Bulk hoodie discount:** ticket SKU and size (`CC-HOOD-NAVY`, `M`) match `inventory.sku` + `inventory.size`, where quantity is 8, against requested quantity 20. `pricing.sku` matches the hoodie, with unit cost $22 and list price $58. These are field-value joins, not declared foreign keys. No invoice, lease, or vendor is directly linked from ticket 103.

The database declares foreign keys from `tickets.invoice_id` to `invoices.id`, `tickets.lease_id` to `leases.id`, and `invoices.vendor_id` to `vendors.id`. Inventory and pricing relationships use matching SKU fields. `desk` and `cash_accounts` provide shop-wide context rather than ticket-specific foreign-key links.

## MCP tools

| Tool | Tables used | Ticket | Why it helps |
|---|---|---|---|
| `get_product_snapshot(sku, size)` | `inventory`, `pricing` | 101 and 103 | Checks requested-size stock, location, unit cost, and list price for the tee order and hoodie discount request. |
| `get_invoice_details(invoice_id)` | `invoices`, `vendors` | 101 | Follows ticket 101's invoice reference to its amount, due date, status, and vendor details. |
| `get_rent_context(lease_id)` | `leases`, `desk` | 102 | Follows ticket 102's lease reference and returns rent terms alongside the shop's operating date. |

## Problem 5: Agent team and MCP wiring

### Agents

| Agent | Prompt file | MCP access | Delegation |
|---|---|---|---|
| Boss | `backend/prompts/boss.md` | All seventeen MCP tools | Can delegate to Inventory, Accounting, Facilities, or Customer Service. |
| Inventory | `backend/prompts/inventory.md` | All seventeen MCP tools | Can delegate to Boss, Accounting, Facilities, or Customer Service. |
| Accounting | `backend/prompts/accounting.md` | All seventeen MCP tools | Can delegate to Boss, Inventory, Facilities, or Customer Service. |
| Facilities | `backend/prompts/facilities.md` | All seventeen MCP tools | Can delegate to Boss, Inventory, Accounting, or Customer Service. |
| Customer Service | `backend/prompts/customer_service.md` | All seventeen MCP tools | Can delegate to Boss, Inventory, Accounting, or Facilities. |

The five prompt files contain the user's role instructions and are loaded from disk for each agent invocation. Each agent is configured for the same direct OpenAI model `gpt-6-luna`. PydanticAI uses its Responses API model path for `openai:gpt-6-luna`, which supports tool calls. `backend/team.py` reads `OPENAI_API_KEY` from the assignments-level `.env` into the hw5 process with `override=False`; it never copies or edits that shared file. The saved OpenRouter profile has a different model and is not used here. The course's Portkey requirement is recorded separately in the assignment and is not used by this runtime.

The saved role instructions route work as follows:

- **Boss:** Read the ticket and choose only the specialists needed. Verify specialist results before resolving. Surface missing information and approval.
- **Inventory:** Check requested SKU, size, quantity, price, vendor, linked invoice, and vendor lead time. Coordinate open unpaid invoices with Accounting, and distinguish orders from received stock.
- **Accounting:** Check cash, invoices, payments, margins, and `desk.date_today` through MCP. Prepare payments and purchases for approval, reject negative cash, verify approved writes, and do not add sales revenue.
- **Facilities:** Check lease and rent timing using MCP, ask Accounting to check cash and prepare any payment, and never invent terms or contact the landlord.
- **Customer Service:** Draft replies from ticket facts and verified employee/MCP information. Confirm availability, pricing, discounts, and decisions with the relevant agents. Never send messages or promise unverified stock, delivery, or discounts.

### MCP tools

| Tool | Tables used | Ticket | Purpose and write boundary |
|---|---|---|---|
| `get_product_snapshot(sku, size)` | `inventory`, `pricing` | 101, 103 | Reads stock, location, unit cost, and list price for the requested size. |
| `get_invoice_details(invoice_id)` | `invoices`, `vendors` | 101 | Reads invoice status, amount, dates, vendor, and lead time. |
| `get_invoice_payment_details(invoice_id)` | `payments` | 101 | Reads recorded payments linked to one invoice. |
| `get_rent_context(lease_id)` | `leases`, `desk` | 102 | Reads lease terms with the database's operating date. |
| `get_ticket_context(ticket_id)` | `tickets`, `invoices`, `vendors`, `leases` | 101, 102, 103 | Reads a ticket and its linked invoice/vendor or lease facts. |
| `get_cash_snapshot()` | `cash_accounts`, `payments` | 101, 102, 103 | Reads balances and recorded outflows before any payment proposal. |
| `get_vendor_details(vendor_id)` | `vendors`, `invoices` | 101, 103 | Reads specialty and lead time plus open invoices that block shipping. |
| `get_rent_payment_details(lease_id)` | `rent_payments` | 102 | Verifies recorded payments for each lease due date after a rent payment. |
| `get_purchase_details(ticket_id)` | `purchases`, `vendors` | 101, 103 | Verifies the explicitly selected vendor and order status without implying delivery. |
| `get_inventory_receipt_details(receipt_reference)` | `inventory_receipts` | Separate simulation | Reads the durable receipt, simulation source label, approval, and stock transition. |
| `advance_simulated_shop_date(new_date, approved_by, approval_reference)` | `desk` | Separate simulation | Advances the single simulated shop date only forward after exact human approval. |
| `record_invoice_payment(invoice_id, account, amount, approved_by, approval_reference)` | `invoices`, `payments`, `cash_accounts`, `desk` | 101 | Records a full invoice payment only when the exact MCP arguments were pre-approved by the trusted caller, the invoice is open, cash is sufficient, and no payment is already recorded. |
| `record_rent_payment(lease_id, account, amount, approved_by, approval_reference)` | `leases`, `rent_payments`, `payments`, `cash_accounts`, `desk` | 102 | Records the lease's current due amount and its cash outflow atomically after exact human approval. One payment per lease due date is allowed. |
| `record_purchase_order(ticket_id, vendor_id, sku, size, quantity, quoted_unit_cost, total_amount, supplier_quote_reference, account, approved_by, approval_reference)` | `tickets`, `vendors`, `pricing`, `purchases`, `payments`, `cash_accounts`, `desk` | 101, 103 | Records an exact approved purchase at a human-verified quote. Product cost is not used as a supplier quote. Vendor is explicitly selected, status remains ordered, and inventory is unchanged. |
| `set_inventory_quantity(sku, size, quantity, reason, approved_by, approval_reference)` | `inventory` | 101, 103 | Changes one recorded quantity only when the exact MCP arguments were pre-approved by the trusted caller. Negative quantities are rejected. |
| `record_inventory_receipt(sku, size, received_quantity, receipt_date, source_label, receipt_reference, approved_by, approval_reference)` | `inventory_receipts`, `inventory`, `desk` | Separate simulation | Checks date against `desk.date_today`, then atomically stores an exactly approved human-input simulation receipt and increases matching stock. Duplicate receipts are rejected. |
| `update_ticket_status(ticket_id, status, approved_by, approval_reference)` | `tickets` | 101, 102, 103 | Changes a ticket status only when the exact MCP arguments were pre-approved by the trusted caller. |

The simulated shop clock is separate from the computer date. A receipt dated after `desk.date_today` is rejected until an exactly approved forward-only clock advance is recorded through MCP.

The server queries `data/campus_customs_new.db`. All agent reads and database updates use MCP tools. `backend/team.py` applies a second write gate: a write tool call is blocked unless the trusted caller supplied a matching `ApprovedToolAction` with the exact tool name and full arguments. The caller identity must match the action's `approved_by` value, and agent role names are rejected as approvers. Approval records are consumed once. No email, vendor contact, customer contact, or external payment tool exists.

### Safety and token limits

- Treat database records as the only source of shop facts. Use `desk.date_today` when comparing due dates. Do not claim a ticket is resolved without verified MCP results and an approved status update.
- Never send customer or vendor messages, make external commitments, or send a payment. Keep any proposed messages as drafts for a human.
- A human must approve the exact invoice/rent payment, purchase order, inventory change, simulated receipt, or ticket status through the trusted caller before its MCP write action is enabled. Agents cannot approve themselves. `approved_by` and `approval_reference` are audit fields, not a substitute for the caller's exact-argument gate.
- Rent payments are tied to the lease's recorded `next_due` date and cannot be duplicated for that due date. The rent ledger, generic payment ledger, and cash debit commit in one transaction.
- Purchases must match the ticket's SKU and size, human-verified supplier quote, explicit vendor selection, quantity, total, and account. The selected vendor must exist and have no open unpaid invoices. The product cost in `pricing` supports margin calculations only and is not evidence of a supplier quote or a vendor-to-SKU relationship. The purchase row, payment ledger, and cash debit commit in one transaction. Purchases stay `ordered`, never change `inventory`, and do not imply that delivery happened or that the customer request is physically fulfilled.
- Reject negative inventory quantities, payments above available cash, non-full invoice payment amounts, duplicate invoice payments, and payments for invoices that are not open. The payment update is one SQLite transaction. Receipt writes compare the receipt date to `desk.date_today`, require the exact simulation source label and unique references, and atomically save receipt provenance with the stock increase. They never change cash, ticket status, purchases, or invoices.
- Every agent is configured for the selected `gpt-6-luna` model and receives `openai_reasoning_effort="none"` and the 1,000-token provider output cap on every request. The same settings carry through delegation and each fresh approval-resume run. Offline mocked `/v1/responses` captures verify `reasoning.effort: "none"` and `max_output_tokens: 1,000` for all five roles. Runtime key: `OPENAI_API_KEY`, loaded from the assignments-level `.env` into the hw5 process only. No Portkey or provider/model fallback is configured.
- Per top-level run: at most 12 provider requests total across Boss, delegates, and validation retries, at most 8 agent invocations including Boss (therefore at most 7 delegations), delegation depth 4, 12 successful MCP tool calls, and 4,000 task characters. Each agent invocation has a cumulative 10,000 input-token limit checked before requests and a 1,000 output-token UsageLimits threshold checked after provider responses. That output threshold is not a provider billing cap and may detect an overage only after a response. `backend/smoke.py` narrows this to 3 model requests, 1 MCP read, 0 delegations, 5,000 input tokens, 1,500 output tokens per response, and 3,000 aggregate output tokens, with every write tool blocked. It uses OpenAI Responses `gpt-6-luna` with `openai_reasoning_effort="none"` and requires a validated `AgentReply` based on the MCP result.
- `output/audit_trail.json` is append-only. Each actual PydanticAI graph node, delegation start/completion, and run boundary is recorded with run ID, timestamp, agent, and ticket ID. It is created on the first actual run. No audit event is fabricated during setup.

### Shared prompt rules

- **Boss:** Route each ticket to the relevant specialist, use MCP evidence before conclusions, coordinate delegations, require human approval for money or updates, and leave each ticket open until its work is verifiably complete.
- **Inventory:** Check matching SKU and size in `inventory` and `pricing`, distinguish stock on hand from requested quantity, check the linked vendor and invoice before any restock plan, and account for the recorded rule that a vendor will not ship while an invoice is open and unpaid.
- **Accounting:** Read `cash_accounts`, `payments`, and the invoice through MCP before proposing payment. Use the recorded balance, reject any action that would make cash negative, pay only an open invoice at its exact full amount through the approved MCP operation, and use `desk.date_today` for timing. Prepare exact rent and purchase proposals, never approve them, and verify resulting records and cash through MCP.
- **Facilities:** Use MCP lease facts and `desk.date_today` to assess rent timing. Report the lease's recorded monthly amount and due date without inventing lease terms, payments, or landlord contact. Verify any approved rent payment through `get_rent_payment_details` and `get_cash_snapshot`.
- **Customer Service:** Base availability and price statements on MCP inventory/pricing facts, do not promise unavailable stock or an unapproved discount, do not send messages or make commitments, and leave customer communication for human review.

All five prompt files also direct the agents to keep shop facts and updates behind MCP, preserve exact human approval checks, avoid unverified claims, and report missing information rather than pretending work is finished.

## Problem 7: FastAPI backend

Start the local API from the project root with `.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`. It listens on localhost port 8000 and permits the Vite origin `http://localhost:5174`. The API sends shop reads and writes through the local MCP server. The agent run route is read-only. Its `AgentReply.pending_actions` are saved in `output/approval_requests.sqlite3`, a separate workflow queue that is ignored by Git. No endpoint resolves a ticket when an agent loop ends.

| Method and URL | Purpose |
|---|---|
| `GET /health` | Return a simple local service status. |
| `GET /runtime-settings` | Return the active direct provider, model, reasoning effort, and safe request/token ceilings. It never returns credential values. |
| `GET /tickets` | Read tickets 101, 102, and 103 with their current statuses and linked facts through MCP. |
| `POST /tickets/{ticket_id}/run` | Run the existing Boss team read-only on one ticket, return the validated agent result and queue any fact-checked payment or purchase proposals. It does not update ticket status. |
| `GET /events?limit=50` | Return the most recent actual agent loop and completion events from `output/audit_trail.json`. The limit is 1 to 200. |
| `GET /approvals/pending` | Present pending and blocked invoice, rent, and purchase requests, including missing facts and any required human quote fields. |
| `POST /approvals/{request_id}/approve` | Require a human name, explicit confirmation, and an exact copy of the proposed arguments. Purchases also require human confirmation of supplier selection, a verified quote reference, quoted unit price, and exact total. Revalidate facts and cash through MCP, atomically claim the request once, consume a matching `ApprovedToolAction`, and execute the MCP write once. |
| `GET /cash/balance` | Return the checking balance and date from MCP. |
| `POST /database/reset` | Replace the working database with the untouched original and cancel pending or blocked approvals. Completed approval history remains in the separate workflow queue. |
| `POST /audit-runs/{run_id}/recover-proposals` | Recover only structured pending actions from validated specialist results recorded in a failed audit run. Recheck current facts through MCP, deduplicate, and stage them as incomplete-run requests. It performs no payment, purchase, status update, or approval. |

FastAPI's generated interactive API docs are available at `/docs` and the schema at `/openapi.json`.

## Problem 8: React dashboard

Start FastAPI from the project root with the command above, then start Vite from `frontend` with `npm install` followed by `npm run dev`. Vite uses port 5174 to match the API's CORS allowlist. Open `http://localhost:5174`. The frontend reads tickets, audit events, pending approvals, and checking balance from `http://localhost:8000`. Build it with `npm run build` from `frontend`.

Append `?demo=1` to the frontend URL for clearly labelled browser-local mock data. Mock runs, approvals, error injection, blocked purchase results, and cash changes stay in local browser storage. Demo mode does not call FastAPI, MCP, or the agents. The dashboard shows per-employee contributions from the audit events, refreshes live events and ticket status during an active run, and only labels a ticket resolved when `GET /tickets` confirms that status. Purchase approvals remain explicitly ordered, never delivered.


## Problem 9: Local verification and live-run preview

For the real local dashboard, keep the existing app on port 5173 running. From the hw5 project root start the API with `.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`. In a second terminal run `cd frontend`, `npm install`, then `npm run dev`. Vite binds to `127.0.0.1:5174` with strict port selection. Open `http://localhost:5174` without `?demo=1`. FastAPI allows exactly the dashboard origin `http://localhost:5174`.

Problem 9's live-run previews are in `output/problem9_preview.md`. Tickets 101 and 102 have had actual runs, and the invoice 501 and rent payment requests were separately approved and executed by the human. MCP-verified checking is $160 after $3,240 in recorded outflows. These results are appended in Ticket 101 and 102 Actual and Cash. Both database files and audit history are preserved. The separate ticket 103 preview uses the current $160 balance and authorizes no call or write. Expected plans and Reflection remain unchanged.

Offline API tests use mocked team replies and disposable database copies. They exercise exact approval, resumed ticket runs, blocked purchase cash, and reset from the untouched original. Reset restores checking to $3,400, removes the working-copy rent and purchase ledgers by restoring the baseline database, and leaves `output/audit_trail.json` unchanged.


### Problem 9 runtime settings correction

 `backend/main.py` exposes the safe active settings at `GET /runtime-settings` without credential values and explicitly passes the shared `openai_reasoning_effort="none"` setting for every ticket run. Delegated `_run_agent` calls share `TeamDeps` for team-wide counters, while each gets a fresh PydanticAI `UsageLimits` and `RunUsage` for its own invocation. Per-invocation token usage is recorded once, then team totals sum those records. A child does not receive the parent's `ctx.usage`, which would make per-agent records overlap. Direct OpenAI Responses requests carry `max_output_tokens: 1000` per response. The PydanticAI 5,000 input and 1,000 output thresholds apply to each agent invocation, with input checked before a request and output checked after a response. The global 8-provider-request counter includes retries and delegations, and resets for each new start or resume. MCP calls use a separate shared counter. See `output/problem9_preview.md` for the current bounded-run ceilings.


## Problem 9 workflow repair

Ticket run instructions now explicitly route 101 through Inventory, Accounting and Customer Service; 102 through Facilities with Accounting; and 103 through Inventory, Accounting and Customer Service. Specialists still generate replies from MCP facts. The API gathers unique proposals across specialist and Boss replies, validates each against MCP facts, and returns blocked proposals with their exact rejection reason. Payment and purchase writes remain behind one-use exact human approval. Usage is included in success audit/API responses and failure audit/API details. Team totals sum each agent invocation once, including delegates. Historical runs without saved token totals remain unknown. Failure payloads retain usage in FastAPI error detail, which the frontend keeps and displays as recorded usage when available. This successful/error-path retention is covered by mocked regression tests, not evidence that a live model will propose a payment.

### Runtime failure repairs (offline verified)

`delegate_to_agent` builds a role-specific target enum, excluding the current agent and retaining the other four. Invalid runtime targets return PydanticAI `ModelRetry` validation feedback within the existing request/retry budget. The offline Boss ? Facilities reproduction confirms the earlier PermissionError came from `guard_mcp_write` in `backend/team.py`, which rejects every MCP write during a read-only run before SQLite. Failures return a stable code and `filename:function:line`, without exception text, keys or raw reasoning. The code is `READ_ONLY_MCP_WRITE_BLOCKED`. Approval and permission checks are unchanged.

The offline three-read Inventory reproduction records 3 requests, 3 MCP reads, 3,900 input tokens and 120 output tokens before the next preflight estimate crosses its 5,000 per-agent invocation limit. Boss usage is a separate invocation. A regression also verifies a 6,400-token team total succeeds when each agent invocation remains below 5,000. Token-limit failures record the numeric invocation limit and preflight count without storing prompts or raw provider output. Shortened instructions and ticket routing pass verified facts downstream and avoid unnecessary repeated reads. Tests use PydanticAI, MCP stdio and FastAPI with temporary databases and audit files. They verify code paths, not live model behavior.



Failed parent runs retain structured proposals from specialists that already completed with validated `AgentReply` output. FastAPI sends them through the same proposal normalization, MCP fact checks, deduplication and approval queue as successful runs. The approval card labels them as coming from an incomplete run and identifies the source agent. They remain unapproved, and run failure never changes ticket status.

`POST /audit-runs/{run_id}/recover-proposals` recovers candidates only from structured `pending_actions` in validated agent completion audit entries belonging to a failed run. It rejects missing business terms through the same exact-argument validation and surfaces the missing fields. Model-authored `approved_by` and `approval_reference` values are discarded because only the human approval route can supply them. Recovery is idempotent and executes no writes.


## Problem 9 final evidence (2026-10-05)

`output/resolved_tickets.json` records each final status, actual structured agent contributions available in the audit, human approvals, and the MCP-verified outcome. `output/resolved_board.html` contains inline JPEG captures from the real React dashboard at `http://localhost:5174` with demo mode off. The dashboard showed all three tickets resolved and checking at $160. No agent run, approval, or shop write was triggered to capture these images.

Ticket 101's live agent run `32644692-4fc7-4376-8b8f-6e5f925d77f8` failed at the Boss step after Inventory and Accounting produced validated AgentReply records. Accounting's invoice proposal was recovered from that failed run and later executed under approved request `89b5d769-9dce-4aaf-a1ef-03516d509432`. A separately approved simulation then recorded one CC-TEE-WHITE size S receipt dated 2026-09-07 (stock 0 to 1), adjusted stock from 1 to 0 for simulated fulfillment, and resolved the ticket. The receipt source is human-provided simulation input, not verified supplier evidence. No actual customer handoff is established.

Ticket 102's live run `4e0628a8-9d0b-4a24-bbad-e3ce9ac27f41` failed with `AGENT_INPUT_OR_USAGE_LIMIT_EXCEEDED`. Facilities did not return a validated reply. Accounting's structured $2,400 rent proposal survived the incomplete run, was human-approved, and was executed once under request `43e294f2-9f0c-48d6-ab2a-b5b30668a3a6`. MCP confirms the rent record and $160 balance. A separate approved status action resolved the notice. No landlord contact is recorded.

Ticket 103's run `78216031-bb8b-4d9c-be16-ad65855fb42c` completed with a validated Boss AgentReply. Inventory reported 8 hoodies available against 20 requested. Accounting recommended 10% off, $52.20 each and $1,044 total, with an indicative $604 gross margin using the internal $22 recorded cost. Customer Service drafted a reply. The human-approved status change closed the price-review request only. It does not establish customer acceptance, a sale, or fulfillment. The later 12-hoodie receipt was a separate simulation and is not supplier evidence or Ticket 103 fulfillment.

The final MCP cash snapshot is $160 checking. Its reconciliation is `$3,400 - $840 - $2,400 = $160`, with both outflows verified in MCP. Reset evidence is inconclusive: the audit trail contains no explicit reset event for the three-ticket sequence. The early $3,400 balance is evidence of the observed balance, not proof of a reset. Earlier Actual entries remain intact; the latest outcomes are appended in `output/desk_tickets.html`.
