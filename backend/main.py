"""Local FastAPI board API for ticket runs and human-approved MCP actions."""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from .audit import AUDIT_PATH, append_audit_step
from .models import (
    ApprovedToolAction,
    PendingActionProposal,
    TeamRequest,
    TeamRunLimits,
    consume_approved_action,
)
from .team import (
    MCP_SERVER_PATH, OPENAI_REASONING_EFFORT, PROJECT_ROOT, run_team,
)


ORIGINAL_DATABASE_PATH = PROJECT_ROOT / "data" / "campus_customs.db"
WORKING_DATABASE_PATH = PROJECT_ROOT / "data" / "campus_customs_new.db"
APPROVAL_STORE_PATH = PROJECT_ROOT / "output" / "approval_requests.sqlite3"
TICKET_IDS = (101, 102, 103)
AGENT_IDENTITIES = {"boss", "inventory", "accounting", "facilities", "customer_service"}

app = FastAPI(title="Campus Customs Desk API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

_operation_lock = asyncio.Lock()


class TicketRunOptions(BaseModel):
    instruction: str | None = Field(default=None, min_length=1, max_length=1_000)


class ApprovalInput(BaseModel):
    approved_by: str = Field(min_length=1, max_length=200)
    confirm_exact_request: Literal[True]
    expected_arguments: dict[str, object]
    quoted_unit_cost: float | None = Field(default=None, ge=0)
    expected_total_amount: float | None = Field(default=None, gt=0)
    supplier_quote_reference: str | None = Field(default=None, max_length=300)
    supplier_selection_confirmed: bool = False

    @model_validator(mode="after")
    def require_human_approver(self) -> "ApprovalInput":
        name = self.approved_by.strip()
        normalized = name.casefold().replace(" ", "_").replace("-", "_")
        if not name or normalized in AGENT_IDENTITIES:
            raise ValueError("Approver must be a human, not an agent role.")
        return self


@contextmanager
def _approval_connection():
    APPROVAL_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(APPROVAL_STORE_PATH, timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS approval_requests (
                   request_id TEXT PRIMARY KEY,
                   dedupe_key TEXT NOT NULL UNIQUE,
                   run_id TEXT NOT NULL,
                   ticket_id INTEGER NOT NULL,
                   tool_name TEXT NOT NULL,
                   arguments_json TEXT NOT NULL,
                   reason TEXT NOT NULL,
                   status TEXT NOT NULL CHECK (status IN
                       ('pending', 'blocked', 'executing', 'executed', 'failed', 'cancelled')),
                   block_reason TEXT,
                   result_json TEXT,
                   approved_by TEXT,
                   approval_reference TEXT,
                   created_at TEXT NOT NULL,
                   updated_at TEXT NOT NULL,
                   run_incomplete INTEGER NOT NULL DEFAULT 0,
                   source_agent TEXT
               )"""
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(approval_requests)")}
        if "run_incomplete" not in columns:
            connection.execute("ALTER TABLE approval_requests ADD COLUMN run_incomplete INTEGER NOT NULL DEFAULT 0")
        if "source_agent" not in columns:
            connection.execute("ALTER TABLE approval_requests ADD COLUMN source_agent TEXT")
        yield connection
    finally:
        connection.close()


async def call_mcp_tool(tool_name: str, arguments: dict[str, object]) -> dict[str, Any]:
    """Call one existing local MCP tool over the configured stdio transport."""
    client = Client(
        StdioTransport(
            command=sys.executable,
            args=[str(MCP_SERVER_PATH)],
            env={"CAMPUS_CUSTOMS_DB": os.environ.get(
                "CAMPUS_CUSTOMS_DB", str(WORKING_DATABASE_PATH)
            )},
            cwd=str(PROJECT_ROOT),
        ),
        name="campus_customs_fastapi",
    )
    async with client:
        result = await client.call_tool(tool_name, arguments)
    if result.is_error:
        raise RuntimeError(f"MCP tool {tool_name} returned an error.")
    if not isinstance(result.data, dict):
        raise RuntimeError(f"MCP tool {tool_name} returned an unexpected result.")
    return result.data


async def _verify_financial_write(
    tool_name: str, arguments: dict[str, object], result: dict[str, Any]
) -> dict[str, Any]:
    """Read the committed record and checking balance back through MCP."""
    cash = await call_mcp_tool("get_cash_snapshot", {})
    account = result.get("account")
    balance = next(
        (row.get("balance") for row in cash.get("accounts", []) if row.get("name") == account),
        None,
    )
    record: dict[str, Any]
    if tool_name == "record_invoice_payment":
        invoice_result = await call_mcp_tool(
            "get_invoice_details", {"invoice_id": arguments["invoice_id"]}
        )
        record = invoice_result.get("invoice") or {}
        record_verified = record.get("status") == "paid"
    elif tool_name == "record_rent_payment":
        rent_result = await call_mcp_tool(
            "get_rent_payment_details", {"lease_id": arguments["lease_id"]}
        )
        record = next(
            (row for row in rent_result.get("payments", [])
             if row.get("id") == result.get("rent_payment_id")),
            {},
        )
        record_verified = bool(record) and record.get("amount") == result.get("amount")
    else:
        purchase_result = await call_mcp_tool(
            "get_purchase_details", {"ticket_id": arguments["ticket_id"]}
        )
        record = next(
            (row for row in purchase_result.get("purchases", [])
             if row.get("id") == result.get("purchase_id")),
            {},
        )
        record_verified = bool(record) and record.get("status") == "ordered"
    balance_verified = balance is not None and _money_cents(balance, "checking balance") == _money_cents(result.get("new_balance"), "new balance")
    return {
        "verified": record_verified and balance_verified,
        "record": record,
        "checking_balance": balance,
        "record_verified": record_verified,
        "balance_verified": balance_verified,
    }


def _money_cents(value: object, name: str) -> int:
    try:
        decimal_value = Decimal(str(value))
        if not decimal_value.is_finite() or decimal_value < 0:
            raise ValueError(f"{name} must be a nonnegative finite amount.")
        cents = decimal_value * 100
        if cents != cents.to_integral_value():
            raise ValueError(f"{name} must use at most two decimal places.")
        return int(cents)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"{name} must be a valid currency amount.") from exc


def _require_exact_keys(arguments: dict[str, object], keys: set[str]) -> None:
    if set(arguments) != keys:
        missing = sorted(keys - set(arguments))
        unexpected = sorted(set(arguments) - keys)
        parts = []
        if missing:
            parts.append(f"missing required arguments: {', '.join(missing)}")
        if unexpected:
            parts.append(f"unexpected arguments: {', '.join(unexpected)}")
        raise ValueError("; ".join(parts))


async def _facts_for_proposal(
    ticket_id: int, proposal: PendingActionProposal
) -> tuple[bool, str | None]:
    """Recheck proposal facts through MCP before making it approvable."""
    args = proposal.arguments
    tool = proposal.tool_name
    try:
        ticket_result = await call_mcp_tool("get_ticket_context", {"ticket_id": ticket_id})
        ticket = ticket_result.get("ticket") if ticket_result.get("found") else None
        if not isinstance(ticket, dict):
            return False, "ticket facts are missing"
        if ticket.get("status") == "resolved":
            return False, "ticket is already resolved"

        if tool == "record_invoice_payment":
            _require_exact_keys(args, {"invoice_id", "account", "amount"})
            invoice_id = args["invoice_id"]
            if type(invoice_id) is not int or invoice_id != ticket.get("invoice_id"):
                return False, "invoice is not the invoice linked to this ticket"
            invoice_result = await call_mcp_tool("get_invoice_details", {"invoice_id": invoice_id})
            invoice = invoice_result.get("invoice") if invoice_result.get("found") else None
            if not isinstance(invoice, dict):
                return False, "linked invoice facts are missing"
            if str(invoice.get("status", "")).lower() != "open":
                return False, "invoice is not open"
            if _money_cents(args["amount"], "amount") != _money_cents(invoice.get("amount"), "invoice amount"):
                return False, "payment must equal the recorded full invoice amount"
        elif tool == "record_rent_payment":
            _require_exact_keys(args, {"lease_id", "account", "amount"})
            lease_id = args["lease_id"]
            if type(lease_id) is not int or lease_id != ticket.get("lease_id"):
                return False, "lease is not the lease linked to this ticket"
            rent_result = await call_mcp_tool("get_rent_context", {"lease_id": lease_id})
            lease = rent_result.get("lease") if rent_result.get("found") else None
            if not isinstance(lease, dict):
                return False, "lease facts are missing"
            if _money_cents(args["amount"], "amount") != _money_cents(lease.get("monthly_rent"), "recorded rent"):
                return False, "payment must equal the recorded rent amount"
            rent_payments = await call_mcp_tool("get_rent_payment_details", {"lease_id": lease_id})
            if any(item.get("due_date") == lease.get("next_due") for item in rent_payments.get("payments", [])):
                return False, "rent for the recorded due date is already paid"
        else:
            _require_exact_keys(args, {"ticket_id", "vendor_id", "sku", "size", "quantity", "account"})
            if args["ticket_id"] != ticket_id:
                return False, "purchase ticket does not match the requested ticket"
            if args["sku"] != ticket.get("sku") or args["size"] != ticket.get("size"):
                return False, "purchase SKU and size do not match the ticket"
            if type(args["vendor_id"]) is not int or type(args["quantity"]) is not int or args["quantity"] <= 0:
                return False, "vendor or purchase quantity is invalid"
            product_result = await call_mcp_tool(
                "get_product_snapshot", {"sku": args["sku"], "size": args["size"]}
            )
            product = product_result.get("product") if product_result.get("found") else None
            if not isinstance(product, dict):
                return False, "matching product facts are missing"
            shortage = max(0, int(ticket.get("qty") or 0) - int(product.get("qty") or 0))
            if shortage <= 0 or args["quantity"] != shortage:
                return False, "purchase quantity does not match the verified ticket shortage"
            vendor_result = await call_mcp_tool("get_vendor_details", {"vendor_id": args["vendor_id"]})
            vendor = vendor_result.get("vendor") if vendor_result.get("found") else None
            if not isinstance(vendor, dict):
                return False, "selected vendor facts are missing"
            if vendor.get("open_unpaid_invoice_ids"):
                return False, "selected vendor has an open unpaid invoice and will not ship"
        if not isinstance(args.get("account"), str) or not str(args["account"]).strip():
            return False, "cash account is missing"
        cash = await call_mcp_tool("get_cash_snapshot", {})
        account_row = next(
            (row for row in cash.get("accounts", []) if row.get("name") == args["account"]),
            None,
        )
        if account_row is None:
            return False, "cash account facts are missing"
        if tool != "record_purchase_order":
            amount = _money_cents(args.get("amount"), "amount")
            if _money_cents(account_row.get("balance"), "cash balance") < amount:
                return False, "insufficient cash for this exact payment"
        return True, None
    except (RuntimeError, ValueError, KeyError, TypeError) as exc:
        return False, f"required MCP facts could not be verified: {exc}"


def _record_proposal(
    *, run_id: str, ticket_id: int, proposal: PendingActionProposal,
    status: str, block_reason: str | None, incomplete_run: bool = False,
    source_agent: str | None = None,
) -> dict[str, Any]:
    arguments_json = json.dumps(proposal.arguments, sort_keys=True, separators=(",", ":"))
    dedupe_key = hashlib.sha256(
        f"{ticket_id}:{proposal.tool_name}:{arguments_json}".encode("utf-8")
    ).hexdigest()
    now = datetime.now(UTC).isoformat()
    request_id = str(uuid4())
    with _approval_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT OR IGNORE INTO approval_requests
               (request_id, dedupe_key, run_id, ticket_id, tool_name, arguments_json,
                reason, status, block_reason, created_at, updated_at,
                run_incomplete, source_agent)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (request_id, dedupe_key, run_id, ticket_id, proposal.tool_name,
             arguments_json, proposal.reason, status, block_reason, now, now,
             int(incomplete_run), source_agent),
        )
        row = connection.execute(
            "SELECT * FROM approval_requests WHERE dedupe_key = ?", (dedupe_key,)
        ).fetchone()
        if row["status"] in {"pending", "blocked", "cancelled"} and (
            incomplete_run or (status == "pending" and row["status"] in {"blocked", "cancelled"})
        ):
            connection.execute(
                """UPDATE approval_requests SET run_id=?, reason=?, status=?,
                   block_reason=?, run_incomplete=?, source_agent=?, updated_at=?
                   WHERE request_id=?""",
                (run_id, proposal.reason, status, block_reason, int(incomplete_run),
                 source_agent, now, row["request_id"]),
            )
            row = connection.execute(
                "SELECT * FROM approval_requests WHERE request_id = ?", (row["request_id"],)
            ).fetchone()
        connection.commit()
    return _public_request(row)


def _normalize_pending_candidate(candidate: Any) -> tuple[PendingActionProposal, str | None]:
    """Keep the exact business terms while discarding model-authored approval claims."""
    source_agent: str | None = None
    raw = candidate
    if isinstance(candidate, dict) and "proposal" in candidate:
        raw = candidate["proposal"]
        source_agent = candidate.get("source_agent")
    if isinstance(raw, PendingActionProposal):
        raw = raw.model_dump(mode="json")
    if not isinstance(raw, dict):
        raise ValueError("recorded proposal is not a structured object")
    payload = dict(raw)
    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        raise ValueError("recorded proposal is missing structured arguments")
    # These fields are reserved for the trusted approval caller, not the proposal.
    business_arguments = dict(arguments)
    business_arguments.pop("approved_by", None)
    business_arguments.pop("approval_reference", None)
    payload["arguments"] = business_arguments
    return PendingActionProposal.model_validate(payload), source_agent


async def _stage_action_proposals(
    *, run_id: str, ticket_id: int, candidates: list[Any], incomplete_run: bool,
) -> list[dict[str, Any]]:
    """Use one MCP fact-check and dedupe path for successful, failed, and recovered runs."""
    staged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        proposal, source_agent = _normalize_pending_candidate(candidate)
        key = (
            proposal.tool_name,
            json.dumps(proposal.arguments, sort_keys=True, separators=(",", ":")),
        )
        if key in seen:
            continue
        seen.add(key)
        valid, reason = await _facts_for_proposal(ticket_id, proposal)
        staged.append(_record_proposal(
            run_id=run_id,
            ticket_id=ticket_id,
            proposal=proposal,
            status="pending" if valid else "blocked",
            block_reason=reason,
            incomplete_run=incomplete_run,
            source_agent=source_agent,
        ))
    return staged


def _public_request(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "request_id": row["request_id"],
        "run_id": row["run_id"],
        "ticket_id": row["ticket_id"],
        "tool_name": row["tool_name"],
        "arguments": json.loads(row["arguments_json"]),
        "reason": row["reason"],
        "status": row["status"],
        "block_reason": row["block_reason"],
        "created_at": row["created_at"],
        "approval_fields_required": (
            ["supplier selection confirmation", "supplier quote reference", "quoted unit price", "exact total"]
            if row["tool_name"] == "record_purchase_order" else []
        ),
        "incomplete_run": bool(row["run_incomplete"]),
        "source_agent": row["source_agent"],
    }


def _get_request(request_id: str) -> sqlite3.Row | None:
    with _approval_connection() as connection:
        return connection.execute(
            "SELECT * FROM approval_requests WHERE request_id = ?", (request_id,)
        ).fetchone()


def _set_request_status(
    request_id: str, status: str, *, block_reason: str | None = None,
    result: dict[str, Any] | None = None, approved_by: str | None = None,
    approval_reference: str | None = None,
) -> None:
    with _approval_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """UPDATE approval_requests SET status=?, block_reason=?, result_json=?,
               approved_by=COALESCE(?, approved_by),
               approval_reference=COALESCE(?, approval_reference), updated_at=?
               WHERE request_id=?""",
            (status, block_reason, json.dumps(result, sort_keys=True) if result is not None else None,
             approved_by, approval_reference, datetime.now(UTC).isoformat(), request_id),
        )
        connection.commit()


def _claim_request_once(
    request_id: str, *, approved_by: str, approval_reference: str
) -> bool:
    """Atomically claim a pending request so concurrent workers cannot execute it twice."""
    with _approval_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """UPDATE approval_requests SET status='executing', approved_by=?,
               approval_reference=?, updated_at=?
               WHERE request_id=? AND status='pending'""",
            (approved_by, approval_reference, datetime.now(UTC).isoformat(), request_id),
        )
        connection.commit()
        return cursor.rowcount == 1


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/runtime-settings")
async def runtime_settings() -> dict[str, Any]:
    """Expose safe active provider and run ceilings, never credential values."""
    from .team import MODEL_NAME

    limits = TeamRunLimits()
    return {
        "provider": "OpenAI API directly",
        "model": MODEL_NAME.removeprefix("openai:"),
        "openai_reasoning_effort": OPENAI_REASONING_EFFORT,
        "limits": {
            "provider_requests": limits.max_requests,
            "agent_invocations": limits.max_agent_invocations,
            "delegation_depth": limits.max_delegation_depth,
            "mcp_calls": limits.max_mcp_tool_calls,
            "input_tokens_per_invocation": limits.max_input_tokens_per_invocation,
            "provider_output_tokens_per_response": limits.max_output_tokens_per_request,
            "output_tokens_per_invocation": limits.max_output_tokens_per_run,
        },
    }


@app.get("/tickets")
async def list_tickets() -> dict[str, Any]:
    tickets = []
    for ticket_id in TICKET_IDS:
        result = await call_mcp_tool("get_ticket_context", {"ticket_id": ticket_id})
        ticket = result.get("ticket") if result.get("found") else None
        if not isinstance(ticket, dict):
            raise HTTPException(status_code=503, detail=f"Ticket {ticket_id} facts are unavailable through MCP.")
        tickets.append(ticket)
    return {"tickets": tickets}


@app.post("/tickets/{ticket_id}/run")
async def start_ticket_run(ticket_id: int, options: TicketRunOptions | None = None) -> dict[str, Any]:
    if ticket_id not in TICKET_IDS:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    async with _operation_lock:
        context = await call_mcp_tool("get_ticket_context", {"ticket_id": ticket_id})
        ticket = context.get("ticket") if context.get("found") else None
        if not isinstance(ticket, dict):
            raise HTTPException(status_code=503, detail="Ticket facts are unavailable through MCP.")
        if ticket.get("status") == "resolved":
            raise HTTPException(status_code=409, detail="Ticket is already resolved.")
        supplied_instruction = options.instruction.strip() if options and options.instruction else ""
        workflows = {
            101: "Ticket 101 workflow: Delegate to Inventory to verify requested SKU/size/quantity and linked vendor/invoice facts. Delegate to Accounting to evaluate linked invoice 501 as an overdue recorded obligation using desk.date_today and checking cash. If justified, return an exact record_invoice_payment proposal for $840 from checking for human review, with a factual reason. Delegate to Customer Service after those facts to draft a customer response. Payment does not prove delivery. Never claim delivery or availability without evidence.",
            102: "Ticket 102 workflow: Delegate to Facilities to verify lease, landlord, rent and due date. Facilities must involve Accounting to verify cash and prepare the exact $2,400 checking rent payment as a record_rent_payment proposal for human review. Do not pay, contact the landlord, or mark resolved.",
            103: "Ticket 103 workflow: Delegate to Inventory for verified stock and shortage. Delegate to Accounting for a specific bulk discount and margin recommendation based on recorded price and cost, clearly labeling internal recorded cost rather than a supplier quote. Then delegate to Customer Service for a grounded draft using verified facts and recommendation. Missing supplier or delivery evidence blocks restocking promises only, not margin analysis or a draft. Do not invent agreement, quote, or delivery.",
        }
        task = (supplied_instruction + "\n\n" if supplied_instruction else "") + workflows[ticket_id] + "\n\nAcross the work, return every valid exact pending_actions proposal from any specialist, preserve reasons, and include customer drafts in proposed_updates. Do not call write tools, resolve tickets, or make external commitments."
        try:
            report = await run_team(
                TeamRequest(task=task, ticket_id=ticket_id),
                read_only=True,
                openai_reasoning_effort=OPENAI_REASONING_EFFORT,
            )
        except Exception as exc:
            run_id = getattr(exc, "run_id", None)
            candidates = getattr(exc, "validated_pending_actions", []) or []
            staged = await _stage_action_proposals(
                run_id=run_id or "unknown-run",
                ticket_id=ticket_id,
                candidates=candidates,
                incomplete_run=True,
            ) if run_id and candidates else []
            raise HTTPException(
                status_code=502,
                detail={
                    "status": "failed",
                    "reason": "agent_run_failed",
                    "run_id": run_id,
                    "error_type": type(exc).__name__,
                    "error_code": getattr(exc, "error_code", None),
                    "source_location": getattr(exc, "source_location", None),
                    "usage_limit": getattr(exc, "usage_limit", None),
                    "usage": getattr(exc, "actual_usage", None),
                    "usage_totals": getattr(exc, "usage_totals", None),
                    "pending_actions": staged,
                    "pending_actions_source": "incomplete_run",
                },
            ) from exc
        candidates = report.pending_action_candidates or [
            {"proposal": p.model_dump(mode="json")} for p in
            [*report.pending_actions, *report.result.pending_actions]
        ]
        requests = await _stage_action_proposals(
            run_id=report.run_id, ticket_id=ticket_id,
            candidates=candidates, incomplete_run=False,
        )
        return {
            "run_id": report.run_id,
            "ticket_id": ticket_id,
            "ticket_status": ticket["status"],
            "ticket_status_changed": False,
            "summary": report.result.summary,
            "facts": report.result.facts,
            "unresolved_questions": report.result.unresolved_questions,
            "proposed_updates": report.result.proposed_updates,
            "pending_actions": requests,
            "usage": [usage.model_dump(mode="json") for usage in report.usage],
            "usage_totals": {"requests": sum(u.requests for u in report.usage), "tool_calls": sum(u.tool_calls for u in report.usage), "input_tokens": sum(u.input_tokens for u in report.usage), "output_tokens": sum(u.output_tokens for u in report.usage)},
        }


@app.post("/audit-runs/{run_id}/recover-proposals")
async def recover_incomplete_run_proposals(run_id: str) -> dict[str, Any]:
    """Re-stage only structured proposals recorded by validated agents in a failed run."""
    if not AUDIT_PATH.exists():
        raise HTTPException(status_code=404, detail="Audit history is unavailable.")
    try:
        history = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail="Audit history is unavailable or malformed.") from exc
    if not isinstance(history, list):
        raise HTTPException(status_code=503, detail="Audit history must be a JSON list.")
    run_events = [event for event in history if event.get("run_id") == run_id]
    if not any(event.get("step") == "team_run_failed" for event in run_events):
        raise HTTPException(status_code=409, detail="Only a failed run can be recovered.")
    candidates: list[Any] = []
    for event in run_events:
        detail = event.get("detail") or {}
        if event.get("step") == "agent_run_completed" and detail.get("structured_output_valid") is True:
            for proposal in detail.get("pending_actions") or []:
                candidates.append({"source_agent": event.get("agent"), "proposal": proposal})
        if event.get("step") == "team_run_failed":
            candidates.extend(detail.get("completed_pending_actions") or [])
    if not candidates:
        raise HTTPException(status_code=404, detail="No validated structured proposals were recorded for this failed run.")
    ticket_ids = {event.get("ticket_id") for event in run_events if event.get("ticket_id") is not None}
    if len(ticket_ids) != 1 or next(iter(ticket_ids)) not in TICKET_IDS:
        raise HTTPException(status_code=422, detail="The failed run does not identify one supported ticket.")
    ticket_id = int(next(iter(ticket_ids)))
    async with _operation_lock:
        requests = await _stage_action_proposals(
            run_id=run_id,
            ticket_id=ticket_id,
            candidates=candidates,
            incomplete_run=True,
        )
    append_audit_step(
        run_id=run_id,
        agent="system",
        ticket_id=ticket_id,
        step="proposals_recovered_for_review",
        node_type="approval_queue",
        detail={"request_ids": [item["request_id"] for item in requests],
                "request_count": len(requests), "writes_executed": False},
    )
    return {
        "run_id": run_id,
        "ticket_id": ticket_id,
        "incomplete_run": True,
        "writes_executed": False,
        "pending_actions": requests,
    }


@app.get("/events")
async def recent_events(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    if not AUDIT_PATH.exists():
        return {"events": []}
    try:
        history = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail="Audit event history is unavailable or malformed.") from exc
    if not isinstance(history, list):
        raise HTTPException(status_code=503, detail="Audit event history must be a JSON list.")
    return {"events": history[-limit:]}


@app.get("/approvals/pending")
async def pending_approvals() -> dict[str, Any]:
    with _approval_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM approval_requests WHERE status IN ('pending','blocked') ORDER BY created_at"
        ).fetchall()
    return {"requests": [_public_request(row) for row in rows]}


@app.post("/approvals/{request_id}/approve")
async def approve_request(request_id: str, approval: ApprovalInput) -> dict[str, Any]:
    async with _operation_lock:
        row = _get_request(request_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Approval request not found.")
        if row["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail={"status": row["status"], "reason": row["block_reason"] or "request is not pending and cannot execute again"},
            )
        proposed = json.loads(row["arguments_json"])
        if json.dumps(approval.expected_arguments, sort_keys=True, separators=(",", ":")) != row["arguments_json"]:
            raise HTTPException(status_code=409, detail="Approved arguments do not exactly match the pending request.")
        tool_name = row["tool_name"]
        arguments = dict(proposed)
        if tool_name == "record_purchase_order":
            if not approval.supplier_selection_confirmed:
                raise HTTPException(status_code=422, detail="Human confirmation of the explicitly selected supplier is required.")
            if approval.quoted_unit_cost is None or approval.expected_total_amount is None or not (approval.supplier_quote_reference or "").strip():
                raise HTTPException(status_code=422, detail="A human-verified supplier quote reference, quoted unit price, and exact total are required.")
            quantity = int(arguments["quantity"])
            quoted_cents = _money_cents(approval.quoted_unit_cost, "quoted unit price")
            total_cents = _money_cents(approval.expected_total_amount, "exact total")
            if quoted_cents * quantity != total_cents:
                raise HTTPException(status_code=422, detail="Exact total must equal quantity multiplied by the supplier-quoted unit price.")
            arguments.update({
                "quoted_unit_cost": quoted_cents / 100,
                "total_amount": total_cents / 100,
                "supplier_quote_reference": approval.supplier_quote_reference.strip(),
            })
        elif any((approval.quoted_unit_cost is not None, approval.expected_total_amount is not None,
                  approval.supplier_quote_reference is not None, approval.supplier_selection_confirmed)):
            raise HTTPException(status_code=422, detail="Supplier quote fields only apply to a purchase request.")
        approval_reference = f"human-approval:{request_id}"
        arguments.update({"approved_by": approval.approved_by.strip(), "approval_reference": approval_reference})
        try:
            action = ApprovedToolAction(
                tool_name=tool_name, arguments=arguments,
                approved_by=approval.approved_by.strip(),
            )
            valid, reason = await _facts_for_proposal(
                row["ticket_id"],
                PendingActionProposal(tool_name=tool_name, arguments=proposed, reason=row["reason"]),
            )
            if not valid:
                _set_request_status(request_id, "blocked", block_reason=reason)
                raise HTTPException(status_code=409, detail={"status": "blocked", "reason": reason})
            if tool_name == "record_purchase_order":
                cash = await call_mcp_tool("get_cash_snapshot", {})
                checking = next((item for item in cash.get("accounts", []) if item.get("name") == arguments["account"]), None)
                if checking is None or _money_cents(checking.get("balance"), "cash balance") < _money_cents(arguments["total_amount"], "purchase total"):
                    _set_request_status(request_id, "blocked", block_reason="insufficient cash for the exact quoted purchase")
                    raise HTTPException(status_code=409, detail={"status": "blocked", "reason": "insufficient cash for the exact quoted purchase"})
            if not _claim_request_once(
                request_id,
                approved_by=approval.approved_by.strip(),
                approval_reference=approval_reference,
            ):
                raise HTTPException(status_code=409, detail="Approval request was already claimed and cannot execute again.")
            consume_approved_action([action], tool_name, arguments)
            result = await call_mcp_tool(tool_name, arguments)
        except HTTPException:
            raise
        except Exception as exc:
            _set_request_status(request_id, "failed", block_reason=f"approved action failed safely: {type(exc).__name__}")
            raise HTTPException(status_code=502, detail={"status": "failed", "reason": "approved MCP action did not complete"}) from exc
        if result.get("updated") is not True:
            reason = str(result.get("reason", "MCP action was blocked"))
            _set_request_status(request_id, "blocked", block_reason=reason, result=result)
            raise HTTPException(status_code=409, detail={"status": "blocked", "reason": reason, "mcp_result": result})
        try:
            verification = await _verify_financial_write(tool_name, arguments, result)
        except Exception as exc:
            verification = {"verified": False, "reason": f"verification unavailable: {type(exc).__name__}"}
        _set_request_status(
            request_id, "executed", result={"write": result, "verification": verification}
        )
        return {
            "request_id": request_id,
            "status": "executed",
            "result": result,
            "verification": verification,
            "verified": verification.get("verified", False),
        }


@app.get("/cash/balance")
async def checking_balance() -> dict[str, Any]:
    snapshot = await call_mcp_tool("get_cash_snapshot", {})
    checking = next((row for row in snapshot.get("accounts", []) if row.get("name") == "checking"), None)
    if checking is None:
        raise HTTPException(status_code=503, detail="Checking account facts are missing from MCP.")
    return {"account": "checking", "balance": checking["balance"], "date": checking.get("date")}


@app.post("/database/reset")
async def reset_working_database() -> dict[str, Any]:
    async with _operation_lock:
        if not ORIGINAL_DATABASE_PATH.is_file():
            raise HTTPException(status_code=503, detail="Untouched original database is missing.")
        temporary_path = WORKING_DATABASE_PATH.with_name(WORKING_DATABASE_PATH.name + f".{uuid4().hex}.reset")
        try:
            shutil.copy2(ORIGINAL_DATABASE_PATH, temporary_path)
            os.replace(temporary_path, WORKING_DATABASE_PATH)
        except OSError as exc:
            temporary_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="Working database reset failed safely.") from exc
        with _approval_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE approval_requests SET status='cancelled',
                   block_reason='working database reset', updated_at=?
                   WHERE status IN ('pending','blocked')""",
                (datetime.now(UTC).isoformat(),),
            )
            cancelled = cursor.rowcount
            connection.commit()
    return {"reset": True, "source": str(ORIGINAL_DATABASE_PATH.name), "cancelled_pending_requests": cancelled}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)
