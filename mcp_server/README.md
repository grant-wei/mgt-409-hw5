# Campus Customs MCP server

`server.py` defines the FastMCP interface for the Campus Customs team and uses only `data/campus_customs_new.db`. Reads use parameterized `SELECT` queries opened read-only. Write tools use explicit parameterized statements and SQLite transactions where needed. No agent should read or change shop facts through a second database layer.

## Tools

| Tool | Database tables | Ticket | Access |
|---|---|---|---|
| `get_product_snapshot(sku, size)` | `inventory`, `pricing` | 101, 103 | Read |
| `get_invoice_details(invoice_id)` | `invoices`, `vendors` | 101 | Read |
| `get_invoice_payment_details(invoice_id)` | `payments` | 101 | Read, verifies payment ledger rows for one invoice |
| `get_rent_context(lease_id)` | `leases`, `desk` | 102 | Read |
| `get_ticket_context(ticket_id)` | `tickets`, `invoices`, `vendors`, `leases` | 101, 102, 103 | Read |
| `get_cash_snapshot()` | `cash_accounts`, `payments` | 101, 102, 103 | Read |
| `get_vendor_details(vendor_id)` | `vendors`, `invoices` | 101, 103 | Read, specialty/lead time and unpaid-invoice shipping blocker |
| `get_rent_payment_details(lease_id)` | `rent_payments` | 102 | Read, per-due-date verification |
| `get_purchase_details(ticket_id)` | `purchases`, `vendors` | 101, 103 | Read, order status only |
| `get_inventory_receipt_details(receipt_reference)` | `inventory_receipts` | Separate simulation | Read, verifies durable receipt input and stock change |
| `advance_simulated_shop_date(new_date, approved_by, approval_reference)` | `desk` | Separate simulation | Write, exact human approval, forward-only shop-clock advance |
| `record_invoice_payment(invoice_id, account, amount, approved_by, approval_reference)` | `invoices`, `payments`, `cash_accounts`, `desk` | 101 | Write, full invoice only, no negative cash, duplicate payment rejected |
| `record_rent_payment(lease_id, account, amount, approved_by, approval_reference)` | `leases`, `rent_payments`, `payments`, `cash_accounts`, `desk` | 102 | Write, exact rent for recorded due date, atomic cash debit, duplicate period rejected |
| `record_purchase_order(ticket_id, vendor_id, sku, size, quantity, quoted_unit_cost, total_amount, supplier_quote_reference, account, approved_by, approval_reference)` | `tickets`, `vendors`, `pricing`, `purchases`, `payments`, `cash_accounts`, `desk` | 101, 103 | Write, exact human-verified supplier quote and selected vendor, records ordered only, no inventory change |
| `set_inventory_quantity(sku, size, quantity, reason, approved_by, approval_reference)` | `inventory` | 101, 103 | Write, nonnegative quantity required |
| `record_inventory_receipt(sku, size, received_quantity, receipt_date, source_label, receipt_reference, approved_by, approval_reference)` | `inventory_receipts`, `inventory`, `desk` | Separate simulation | Write, exact human approval, date must not be after `desk.date_today`, unique receipt and approval references, atomically records receipt and increments matching stock |
| `update_ticket_status(ticket_id, status, approved_by, approval_reference)` | `tickets` | 101, 102, 103 | Write, status allowlist |

Write tools validate their arguments and database constraints. Rent payment records are unique by lease and recorded due date. A purchase must match a real ticket's SKU and size, a human-verified supplier quote and reference, and a real vendor row selected explicitly by the human with no open unpaid invoice. Product `pricing.unit_cost` is not treated as a supplier quote, and no vendor-to-SKU relationship is inferred. Its `purchases` row, `payments` row, and cash debit are atomic. Purchase status is always `ordered`, on-hand inventory is unchanged, and delivery or customer fulfillment is not inferred. `rent_payments` stores each rent period separately from the general payment ledger.

The separate `advance_simulated_shop_date` tool advances the single simulated `desk.date_today` row only forward and only with exact caller-side approval. It does not perform real-world timekeeping.

`record_inventory_receipt` is a separate human-input simulation workflow. Its source label is fixed to `human-provided simulation input, not verified supplier evidence`. One SQLite transaction validates the receipt date against `desk.date_today`, inserts the durable receipt row with previous/resulting stock and human approval fields, and increases the matching inventory quantity. A duplicate receipt reference, approval reference, or same SKU/size/quantity/date/source tuple is rejected without changing stock. `get_inventory_receipt_details` reads the saved row. The receipt has no ticket or invoice link and does not change cash, ticket status, or purchase records. The write remains blocked in the team unless a trusted caller supplies a one-use `ApprovedToolAction` matching the complete MCP tool name and arguments exactly.

The `rent_payments`, `purchases`, and `inventory_receipts` tables are created inside the first successful approved transaction that needs them. A rejected date, duplicate, or other unsuccessful receipt rolls back schema creation and transaction writes.

The PydanticAI caller blocks every write unless a trusted caller supplies a one-use approval record matching the tool name and every argument exactly. `ApprovedToolAction` requires the caller identity to match `approved_by` and rejects agent role names. Approval fields are audit context and do not independently authorize a write. A payment, purchase, stock change, or ticket status update remains blocked without that caller-side match. No setup step calls a write tool.

Each lookup reports `found: false` and a null record where applicable. The local Codex MCP entry remains named `campus_customs` and launches the project's `.venv` Python interpreter with `server.py`.

## PydanticAI team integration

`backend/team.py` loads the role prompt files in `backend/prompts/` when an agent runs. Boss, Inventory, Accounting, Facilities, and Customer Service all receive this MCP toolset and can delegate to any other role. Direct OpenAI Responses uses `gpt-6-luna`. The process environment takes precedence over project `.env`, followed by the existing assignments-level `.env`. Shared key files are never copied, edited, or committed. The team has no Portkey or alternate-model path.

`backend/smoke.py` is the bounded read-only integration check. Run it from the project root with `.venv\Scripts\python.exe -m backend.smoke`. It asks Inventory to read ticket 101's requested-size product snapshot, then return a validated `AgentReply` based on that result retained in its conversation. It uses the OpenAI Responses API directly with `gpt-6-luna` and `openai_reasoning_effort="none"`. Writes and delegations are disabled, the run allows up to three model requests and one MCP read, each response is capped at 1,500 output tokens, aggregate output at 3,000 tokens, and input at 5,000 tokens. Safe audit metadata records provider status, incomplete reason, response-part counts, and retry reason without raw reasoning or credentials. The smoke verifies a real audit append and unchanged working database hash. It does not resolve the ticket. Actual token usage is printed on success or recorded from the failed run when available.

`backend/main.py` connects the local FastAPI desk to the same MCP server. Ticket runs execute the existing team in read-only mode and stage typed `AgentReply.pending_actions` in a separate approval queue. The human approval route rechecks current facts and balance through MCP, requires the exact proposal plus any separately verified supplier quote, and atomically claims the request before calling a financial write tool. It verifies the committed record and checking balance through MCP afterward. The route never marks a ticket resolved when an agent run finishes. See `output/harness.md` for every backend route and purpose.

Run `.venv\Scripts\python.exe -m unittest backend.financial_tools_test -v` for offline rent, purchase, receipt, exact approval, duplicate, balance, and no-delivery checks. Receipt tests advance `desk.date_today` only in a temporary database copy and make no model requests.

Runtime failure repairs: `delegate_to_agent` now excludes the calling role in its schema while preserving the other four choices. Invalid targets return bounded PydanticAI validation feedback. Read-only MCP writes are still stopped by `guard_mcp_write` in `backend/team.py` before reaching this server. Safe diagnostics expose `READ_ONLY_MCP_WRITE_BLOCKED` and a source location only. Offline three-read tests show Inventory at 3 requests, 3 MCP reads, 3,900 input tokens and 120 output tokens before the existing 5,000 per-agent input threshold prevents the next request. The workflow was reduced to reuse verified facts instead of repeating reads. No limit was raised. Regression tests traverse PydanticAI, MCP stdio and FastAPI using FunctionModel mocks and temporary database/audit files. Mocked behavior is not evidence of live model proposals.

The current offline suite also proves the reduced ticket-101 Inventory path completes as a valid AgentReply with one MCP read. The previous three-read scenario remains as a regression reproduction for the per-invocation token guard. PydanticAI v2.54.0 starts a fresh `RunUsage` for each `_run_agent` invocation unless a caller passes an existing usage object. The team deliberately keeps child usage separate, records each invocation once, and sums those records for the team totals. `UsageLimits` is also created fresh for each invocation. Shared provider-request and MCP-call ceilings remain in `TeamDeps` and are enforced by team-level counters. The regression confirms a 6,400-input-token team total succeeds while every agent invocation remains below 5,000, and confirms a single invocation that crosses 5,000 fails with a safe numeric diagnostic. Mocked behavior is not evidence of live proposal generation.

When a parent fails after a specialist has returned a validated `AgentReply`, the server retains that specialist's structured `pending_actions` and passes them through the same MCP verification, deduplication, and human-review queue as completed runs. The queue marks these requests as incomplete-run proposals and identifies the source agent. It does not approve them or resolve tickets. `POST /audit-runs/{run_id}/recover-proposals` can re-stage exact structured proposals from validated completion entries in an audited failed run. It is idempotent and never runs a payment, purchase, stock write, or status update. Human approval remains a separate exact-action request.
