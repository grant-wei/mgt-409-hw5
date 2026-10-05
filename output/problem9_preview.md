# One-ticket repair retry preview

This is a preview for one fresh ticket 101 run. No paid call, ticket run, approval, database reset or production audit write was made during the repair.

## Runtime and bounded limits

The team uses the OpenAI Responses API directly with `gpt-6-luna`, the existing assignments-level key loaded into the homework process, and `openai_reasoning_effort="none"`. No Portkey or model fallback is configured. Each top-level run is limited to 8 provider requests across Boss, delegates and validation retries, 8 agent invocations including Boss, delegation depth 4, 12 MCP calls and 4,000 task characters. The per-agent input-token limit remains 5,000. The PydanticAI aggregate output threshold remains 1,000 per invocation and is checked after responses. OpenAI receives `max_output_tokens: 1,000` on each response, a provider-enforced cap.

At the request ceiling, a single run could consume at most 40,000 input tokens and 8,000 provider-capped output tokens. The conservative maximum cost is $0.009, rounded up to $0.01, assuming all input is billed at the cache-write rate. This is a bounded estimate, not an account-level billing stop.

## Ticket 101 facts and expected work

The ticket requests one size-S white Bulldog tee (`CC-TEE-WHITE`). The working database records zero on hand, an open $840 invoice 501 due 2026-08-28 to Bulldog Print Co, a five-day vendor lead time, $8 unit cost and $28 list price. Checking starts at $3,400. The agents should use MCP to verify the ticket, stock, invoice and cash. They may prepare the exact $840 invoice payment proposal for human review and draft a customer reply. Payment alone does not establish shipment or delivery. No supplier shipment or delivery confirmation is recorded.

## Approval boundary

This preview authorizes no API call. If separately approved and a valid exact payment proposal is generated, the dashboard will pause for a human to approve or reject that exact action. No payment, purchase, discount or status update is approved here. This single attempt includes no resume. A future approval-resume attempt would need separate review and a new budget.

## Offline repair evidence

The offline regression suite uses mocked provider responses with real PydanticAI agent/delegation, FastMCP stdio and FastAPI paths, and temporary database/audit files. It confirms self-delegation is excluded and invalid targets receive bounded validation feedback. The Boss ? Facilities write attempt is blocked by `guard_mcp_write` with safe code `READ_ONLY_MCP_WRITE_BLOCKED`. The three-read Inventory reproduction records 3 requests, 3 MCP reads, 3,900 input tokens and 120 output tokens before the next preflight would cross the 5,000 per-agent limit. The workflow now avoids unnecessary repeated reads and passes verified facts. These mocked checks are not evidence that a live agent will generate the payment proposal.


# Ticket 102 One-Run Preview

Prepared 2026-10-04 from current MCP-verified cash. No paid call, ticket run, approval, reset, or database write is included.

Invoice 501's exact approval request `89b5d769-9dce-4aaf-a1ef-03516d509432` is recorded as executed. MCP shows one $840 invoice payment from checking on 2026-08-31, and checking is $2,560. Ticket 102 concerns the recorded $2,400 rent due 2026-09-02. If a valid rent proposal is generated and a human separately approves it, checking would become $160. This preview itself authorizes neither API usage nor the rent payment.

The single ticket 102 run is bounded to 8 provider requests across Boss and delegates, 8 agent invocations, delegation depth 4, 12 MCP calls, 5,000 input tokens per invocation and 1,000 output tokens per response. OpenAI Responses uses `gpt-6-luna` directly with reasoning effort `none`; each response carries the provider-enforced `max_output_tokens: 1,000` cap. The 5,000-token invocation threshold is checked after provider responses, so aggregate usage is not provider-capped. At the request ceiling, the conservative maximum is 40,000 input and 8,000 output tokens. Using the existing preview's pricing assumptions, the estimated maximum API cost is $0.01, rounded up from $0.009. This run has no resume allowance.

Expected MCP work: read ticket 102 and its lease/rent payment context, verify checking, and prepare (but never execute) the exact $2,400 rent payment proposal if facts support it. The dashboard should pause for human review. No ticket resolution, landlord contact, or payment is authorized by this preview.


# Ticket 103 One-Run Preview

Prepared 2026-10-05 from MCP-verified ticket, product, and cash facts. This preview authorizes no paid call, ticket run, discount, purchase, payment, or ticket-status change.

Ticket 103 is open. Yale AI Club requests 20 navy hoodies, size M. MCP records 8 in stock, $58 list price, and $22 unit cost. The $22 is an internal recorded cost, not a supplier quote. No vendor is linked to this request, no supplier relationship or quote is recorded, and no delivery evidence is present. MCP shows checking at $160 after the recorded $840 invoice payment and $2,400 rent payment. A hypothetical purchase of the 12-unit shortfall at the recorded cost would be $264, above current cash, and cannot be proposed as a confirmed order without a verified supplier quote.

Expected work: Boss reads `get_ticket_context(103)` and delegates to Inventory for `get_product_snapshot("CC-HOOD-NAVY", "M")`. Accounting uses the verified product facts to recommend a specific 10% bulk discount for human review: $52.20 per hoodie, $30.20 gross margin per hoodie, approximately 57.9% on recorded internal cost. Customer Service drafts a reply using those verified facts. Do not imply customer agreement, offer an unapproved discount, invent a supplier quote, promise restocking or delivery, or treat the proposed reply as a handled ticket or fulfilled order. Cash is read through `get_cash_snapshot()` only if needed for the workflow. No write tool is expected.

The single run uses direct OpenAI Responses with `gpt-6-luna` and reasoning effort `none`. Limits are 8 provider requests shared across retries and delegations, 8 agent invocations including Boss, delegation depth 4, 12 MCP calls, 5,000 input tokens per agent invocation, and a provider-enforced cap of 1,000 output tokens per response. The PydanticAI output threshold is 1,000 per invocation and is checked after responses. No resume is included. The ceiling represents at most 40,000 input and 8,000 output tokens. At the same conservative pricing assumptions as the prior preview, maximum estimated cost is $0.009, rounded up to $0.01. This is an estimate, not an account-level billing stop.


# Ticket 103 Revised One-Run Preview

Prepared 2026-10-05 after the offline runtime-limit regression passed. This preview supersedes the earlier Ticket 103 limit preview. It authorizes no paid call, ticket run, discount, purchase, payment, or ticket-status change.

## Runtime and cost ceiling

The team uses OpenAI Responses directly with `gpt-6-luna`, reasoning effort `none`, and the existing assignments-level API key. No Portkey or model fallback is configured. One run is limited to 12 provider requests shared across Boss and all delegates/retries, 8 agent invocations including Boss, delegation depth 4, 12 MCP calls, 10,000 cumulative input tokens per invocation, and 1,000 provider-capped output tokens per response. The additional PydanticAI output threshold remains 1,000 per invocation and is checked after responses. No resume is included.

The aggregate input ceiling is 80,000 tokens (8 invocations ? 10,000). Provider output is capped at 12,000 tokens (12 requests ? 1,000). Using Standard short-context GPT-6 Luna pricing at the cache-write input rate ($0.125 per million input tokens) and full output rate ($0.50 per million output tokens), the estimated maximum is $0.016, rounded up to **$0.02**. This uses the conservative assumption that all input is billed at the cache-write rate. See [OpenAI API pricing](https://developers.openai.com/api/docs/pricing). The estimate is not an account-level billing stop.

## Ticket facts and planned work

Ticket 103 is open. Yale AI Club requests 20 navy hoodies, size M. Verified records show 8 on hand, a recorded $58 list price, $22 internal unit cost, and $160 checking. The $22 is not a supplier quote. No supplier relationship, supplier quote, delivery confirmation, or customer acceptance is recorded. Boss reads ticket context, then delegates to Inventory for the product snapshot, Accounting for a specific discount and margin recommendation using those verified facts, and Customer Service for a draft using the specialist results. Boss must verify the completed replies before returning a valid `AgentReply`. Expected discount is for human review only. No writes are authorized. Missing supplier/delivery evidence blocks restocking claims but does not block the discount analysis or customer draft.

## Offline evidence

The mocked end-to-end regression runs the existing PydanticAI delegation flow, FastMCP stdio server, and FastAPI ticket route against a temporary database and audit file. It confirms Boss ? Inventory ? Accounting ? Customer Service ? completed Boss `AgentReply`, with Boss usage above 5,000 and below 10,000 input tokens, validated employee outputs retained, customer draft retained, and usage totals counted once. This is mocked evidence, not proof of a future live run. Both project databases, production audit history, Expected and Actual remain untouched by this verification. No paid calls were made.
