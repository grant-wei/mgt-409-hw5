"""Offline FastAPI route and approval-flow checks on disposable DB copies."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from backend import audit, main
from backend.models import AgentReply, PendingActionProposal, TeamRunReport
from mcp_server import server


class BackendRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="hw5-api-test-")
        self.root = Path(self.temp_dir.name)
        self.original = self.root / "campus_customs.db"
        self.working = self.root / "campus_customs_new.db"
        self.queue = self.root / "approval_requests.sqlite3"
        self.audit = self.root / "audit_trail.json"
        shutil.copy2(main.ORIGINAL_DATABASE_PATH, self.original)
        shutil.copy2(main.ORIGINAL_DATABASE_PATH, self.working)
        with closing(sqlite3.connect(self.working)) as connection:
            connection.execute(
                "INSERT INTO vendors (id, name, specialty, lead_days) VALUES (99, 'Offline supplier fixture', 'test only', 1)"
            )
            connection.commit()
        self.paths = (
            main.ORIGINAL_DATABASE_PATH,
            main.WORKING_DATABASE_PATH,
            main.APPROVAL_STORE_PATH,
            main.AUDIT_PATH,
            server.DATABASE_PATH,
            main.call_mcp_tool,
            main.run_team,
            audit.AUDIT_PATH,
        )
        main.ORIGINAL_DATABASE_PATH = self.original
        main.WORKING_DATABASE_PATH = self.working
        main.APPROVAL_STORE_PATH = self.queue
        main.AUDIT_PATH = self.audit
        audit.AUDIT_PATH = self.audit
        server.DATABASE_PATH = self.working
        self.write_calls: list[tuple[str, dict[str, object]]] = []
        self.team_run_kwargs: list[dict[str, object]] = []
        main.call_mcp_tool = self.mock_mcp_tool
        main.run_team = self.mock_team_run
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        self.client.close()
        (
            main.ORIGINAL_DATABASE_PATH,
            main.WORKING_DATABASE_PATH,
            main.APPROVAL_STORE_PATH,
            main.AUDIT_PATH,
            server.DATABASE_PATH,
            main.call_mcp_tool,
            main.run_team,
            audit.AUDIT_PATH,
        ) = self.paths
        self.temp_dir.cleanup()

    async def mock_mcp_tool(self, tool_name: str, arguments: dict[str, object]) -> dict:
        reads = {
            "get_ticket_context": server.get_ticket_context,
            "get_product_snapshot": server.get_product_snapshot,
            "get_invoice_details": server.get_invoice_details,
            "get_rent_context": server.get_rent_context,
            "get_rent_payment_details": server.get_rent_payment_details,
            "get_purchase_details": server.get_purchase_details,
            "get_cash_snapshot": server.get_cash_snapshot,
            "get_vendor_details": server.get_vendor_details,
        }
        writes = {
            "record_invoice_payment": server.record_invoice_payment,
            "record_rent_payment": server.record_rent_payment,
            "record_purchase_order": server.record_purchase_order,
        }
        if tool_name in writes:
            self.write_calls.append((tool_name, arguments.copy()))
            return writes[tool_name](**arguments)
        return reads[tool_name](**arguments)

    async def mock_team_run(self, request, *, read_only: bool = False, **kwargs) -> TeamRunReport:
        self.assertTrue(read_only)
        self.team_run_kwargs.append({"read_only": read_only, **kwargs})
        proposal = self.next_proposal
        proposals = getattr(self, "team_proposals", None)
        proposals = proposals if proposals is not None else ([proposal] if proposal else [])
        usage = getattr(self, "mock_usage", [])
        return TeamRunReport(
            run_id=str(uuid4()),
            result=AgentReply(
                agent="boss",
                summary="Offline mocked ticket analysis.",
                facts=["Facts came from mocked MCP reads."],
                pending_actions=[],
            ),
            usage=usage,
            pending_actions=proposals,
        )

    def set_proposal(self, tool_name: str, arguments: dict[str, object], reason: str) -> None:
        self.next_proposal = PendingActionProposal(
            tool_name=tool_name, arguments=arguments, reason=reason
        )

    def checking_balance(self) -> float:
        with closing(sqlite3.connect(self.working)) as connection:
            return float(connection.execute(
                "SELECT balance FROM cash_accounts WHERE name='checking'"
            ).fetchone()[0])

    def set_balance(self, amount: float) -> None:
        with closing(sqlite3.connect(self.working)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE cash_accounts SET balance=? WHERE name='checking'", (amount,)
            )
            connection.commit()

    def stage_rent_request(self) -> dict:
        self.set_proposal(
            "record_rent_payment",
            {"lease_id": 1, "account": "checking", "amount": 2400.0},
            "Rent is due under the linked lease.",
        )
        response = self.client.post("/tickets/102/run", json={})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["pending_actions"][0]

    def test_runtime_settings_expose_exact_safe_limits(self) -> None:
        response = self.client.get("/runtime-settings")
        self.assertEqual(response.status_code, 200)
        settings = response.json()
        self.assertEqual(settings["provider"], "OpenAI API directly")
        self.assertEqual(settings["model"], "gpt-6-luna")
        self.assertEqual(settings["openai_reasoning_effort"], "none")
        self.assertEqual(settings["limits"], {
            "provider_requests": 12,
            "agent_invocations": 8,
            "delegation_depth": 4,
            "mcp_calls": 12,
            "input_tokens_per_invocation": 10_000,
            "provider_output_tokens_per_response": 1_000,
            "output_tokens_per_invocation": 1_000,
        })
        self.assertNotIn("key", json.dumps(settings).casefold())

    def test_tickets_balance_events_and_frontend_cors(self) -> None:
        self.audit.write_text(
            json.dumps([{"run_id": "mock", "agent": "boss", "step": "agent_run_completed", "detail": {"summary": "mocked"}}]),
            encoding="utf-8",
        )
        tickets = self.client.get("/tickets")
        self.assertEqual(tickets.status_code, 200)
        self.assertEqual([row["id"] for row in tickets.json()["tickets"]], [101, 102, 103])
        self.assertTrue(all(row["status"] == "open" for row in tickets.json()["tickets"]))
        self.assertEqual(self.client.get("/cash/balance").json()["balance"], 3400.0)
        self.assertEqual(self.client.get("/events?limit=1").json()["events"][0]["run_id"], "mock")
        preflight = self.client.options(
            "/tickets/102/run",
            headers={"Origin": "http://localhost:5174", "Access-Control-Request-Method": "POST"},
        )
        self.assertEqual(preflight.headers.get("access-control-allow-origin"), "http://localhost:5174")
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})

    def test_exact_human_rent_approval_is_single_use_and_does_not_resolve_ticket(self) -> None:
        pending = self.stage_rent_request()
        args = pending["arguments"]
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(self.client.get("/approvals/pending").json()["requests"][0]["request_id"], pending["request_id"])
        bad_args = {**args, "amount": 1.0}
        rejected = self.client.post(
            f"/approvals/{pending['request_id']}/approve",
            json={"approved_by": "Grant", "confirm_exact_request": True, "expected_arguments": bad_args},
        )
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(self.checking_balance(), 3400.0)
        self.assertEqual(self.write_calls, [])
        agent_approval = self.client.post(
            f"/approvals/{pending['request_id']}/approve",
            json={"approved_by": "Accounting", "confirm_exact_request": True, "expected_arguments": args},
        )
        self.assertEqual(agent_approval.status_code, 422)
        approval = self.client.post(
            f"/approvals/{pending['request_id']}/approve",
            json={"approved_by": "Grant", "confirm_exact_request": True, "expected_arguments": args},
        )
        self.assertEqual(approval.status_code, 200, approval.text)
        self.assertEqual(approval.json()["status"], "executed")
        self.assertEqual(self.checking_balance(), 1000.0)
        self.assertEqual(self.client.post(
            f"/approvals/{pending['request_id']}/approve",
            json={"approved_by": "Grant", "confirm_exact_request": True, "expected_arguments": args},
        ).status_code, 409)
        self.assertEqual(len(self.write_calls), 1)
        self.assertEqual(server.get_ticket_context(102)["ticket"]["status"], "open")

    def stage_purchase_request(self) -> dict:
        self.set_proposal(
            "record_purchase_order",
            {"ticket_id": 103, "vendor_id": 99, "sku": "CC-HOOD-NAVY", "size": "M", "quantity": 12, "account": "checking"},
            "The verified stock is short of the requested quantity.",
        )
        response = self.client.post("/tickets/103/run", json={})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["pending_actions"][0]

    def test_purchase_requires_human_verified_quote_and_keeps_order_undelivered(self) -> None:
        pending = self.stage_purchase_request()
        request_body = {
            "approved_by": "Grant",
            "confirm_exact_request": True,
            "expected_arguments": pending["arguments"],
            "supplier_selection_confirmed": True,
            "supplier_quote_reference": "OFFLINE-QUOTE-77",
            "quoted_unit_cost": 25.00,
            "expected_total_amount": 300.00,
        }
        response = self.client.post(f"/approvals/{pending['request_id']}/approve", json=request_body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["result"]["status"], "ordered")
        self.assertEqual(response.json()["result"]["quoted_unit_cost"], 25.0)
        self.assertEqual(self.checking_balance(), 3100.0)
        self.assertEqual(server.get_product_snapshot("CC-HOOD-NAVY", "M")["product"]["qty"], 8)
        self.assertEqual(server.get_ticket_context(103)["ticket"]["status"], "open")
        purchase = server.get_purchase_details(103)["purchases"][0]
        self.assertEqual(purchase["supplier_quote_reference"], "OFFLINE-QUOTE-77")

    def test_insufficient_cash_blocks_purchase_before_mcp_write(self) -> None:
        self.set_balance(160.0)
        pending = self.stage_purchase_request()
        response = self.client.post(
            f"/approvals/{pending['request_id']}/approve",
            json={
                "approved_by": "Grant", "confirm_exact_request": True,
                "expected_arguments": pending["arguments"],
                "supplier_selection_confirmed": True,
                "supplier_quote_reference": "OFFLINE-QUOTE-22",
                "quoted_unit_cost": 22.0, "expected_total_amount": 264.0,
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("insufficient cash", response.json()["detail"]["reason"])
        self.assertEqual(self.checking_balance(), 160.0)
        self.assertEqual(self.write_calls, [])
        with closing(sqlite3.connect(self.working)) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='purchases'"
            ).fetchone()
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] if table else 0,
                0,
            )


    def test_specialist_proposal_propagation_dedup_rejection_and_usage_totals(self) -> None:
        invoice = PendingActionProposal(
            tool_name="record_invoice_payment",
            arguments={"invoice_id": 501, "account": "checking", "amount": 840.0},
            reason="Recorded open overdue obligation, subject to human approval.",
        )
        self.team_proposals = [invoice, invoice]
        self.next_proposal = invoice
        from backend.models import AgentUsage
        self.mock_usage = [
            AgentUsage(agent="accounting", requests=2, tool_calls=1, input_tokens=300, output_tokens=80),
            AgentUsage(agent="boss", requests=1, tool_calls=2, input_tokens=100, output_tokens=40),
        ]
        response = self.client.post("/tickets/101/run", json={})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(len(body["pending_actions"]), 1)
        self.assertEqual(body["pending_actions"][0]["status"], "pending")
        self.assertEqual(body["pending_actions"][0]["reason"], invoice.reason)
        self.assertEqual(body["usage_totals"], {"requests": 3, "tool_calls": 3, "input_tokens": 400, "output_tokens": 120})

        invalid = PendingActionProposal(
            tool_name="record_invoice_payment",
            arguments={"invoice_id": 501, "account": "checking", "amount": 1.0},
            reason="Deliberately invalid offline test proposal.",
        )
        self.team_proposals = [invalid]
        self.next_proposal = None
        rejected = self.client.post("/tickets/101/run", json={})
        self.assertEqual(rejected.status_code, 200, rejected.text)
        blocked = rejected.json()["pending_actions"][0]
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["block_reason"], "payment must equal the recorded full invoice amount")

    def test_failed_audit_proposal_recovery_is_validated_labelled_and_idempotent(self) -> None:
        run_id = "failed-audit-invoice-run"
        reason = "Recorded open invoice 501 is overdue. Checking is sufficient. Human review required."
        proposal = {
            "tool_name": "record_invoice_payment",
            "arguments": {
                "invoice_id": 501, "account": "checking", "amount": 840,
                "approved_by": "human approval desk",
                "approval_reference": "pending human approval",
            },
            "reason": reason,
        }
        self.audit.write_text(json.dumps([
            {"run_id": run_id, "ticket_id": 101, "agent": "accounting",
             "step": "agent_run_completed", "detail": {
                 "structured_output_valid": True, "pending_actions": [proposal]}},
            {"run_id": run_id, "ticket_id": 101, "agent": "boss",
             "step": "team_run_failed", "detail": {"actual_usage": []}},
        ]), encoding="utf-8")
        first = self.client.post(f"/audit-runs/{run_id}/recover-proposals")
        self.assertEqual(first.status_code, 200, first.text)
        action = first.json()["pending_actions"][0]
        self.assertEqual(action["status"], "pending")
        self.assertTrue(action["incomplete_run"])
        self.assertEqual(action["source_agent"], "accounting")
        self.assertEqual(action["arguments"], {"invoice_id": 501, "account": "checking", "amount": 840})
        self.assertEqual(action["reason"], reason)
        second = self.client.post(f"/audit-runs/{run_id}/recover-proposals")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["pending_actions"][0]["request_id"], action["request_id"])
        self.assertEqual(len(self.client.get("/approvals/pending").json()["requests"]), 1)
        self.assertEqual(self.checking_balance(), 3400.0)
        self.assertEqual(self.write_calls, [])
        with closing(sqlite3.connect(self.working)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 0)

    def test_audit_recovery_reports_exact_missing_business_arguments(self) -> None:
        run_id = "failed-audit-missing-amount"
        self.audit.write_text(json.dumps([
            {"run_id": run_id, "ticket_id": 101, "agent": "accounting",
             "step": "agent_run_completed", "detail": {
                 "structured_output_valid": True,
                 "pending_actions": [{"tool_name": "record_invoice_payment",
                     "arguments": {"invoice_id": 501, "account": "checking"},
                     "reason": "Recorded invoice 501 is open."}]}},
            {"run_id": run_id, "ticket_id": 101, "agent": "boss",
             "step": "team_run_failed", "detail": {}},
        ]), encoding="utf-8")
        response = self.client.post(f"/audit-runs/{run_id}/recover-proposals")
        self.assertEqual(response.status_code, 200, response.text)
        action = response.json()["pending_actions"][0]
        self.assertEqual(action["status"], "blocked")
        self.assertIn("missing required arguments: amount", action["block_reason"])
        self.assertEqual(self.write_calls, [])

    def test_failed_run_retains_actual_usage_in_api_error(self) -> None:
        class RunFailure(RuntimeError):
            actual_usage = [{"agent": "boss", "requests": 1, "tool_calls": 0, "input_tokens": 91, "output_tokens": 17, "details": {}}]
            usage_totals = {"requests": 1, "tool_calls": 0, "input_tokens": 91, "output_tokens": 17}
        async def fail_run(*args, **kwargs):
            raise RunFailure("mocked failure")
        main.run_team = fail_run
        response = self.client.post("/tickets/101/run", json={})
        self.assertEqual(response.status_code, 502)
        detail = response.json()["detail"]
        self.assertEqual(detail["usage_totals"]["input_tokens"], 91)
        self.assertEqual(detail["usage"][0]["output_tokens"], 17)

    def test_approval_resume_sequence_and_reset_preserve_audit_and_ledgers(self) -> None:
        # This represents the offline start -> human approval -> resumed run flow.
        audit_history = [{"event_id": "offline-sentinel", "step": "prior_history"}]
        self.audit.write_text(json.dumps(audit_history), encoding="utf-8")

        self.set_proposal(
            "record_invoice_payment",
            {"invoice_id": 501, "account": "checking", "amount": 840.0},
            "Ticket 101's linked open invoice.",
        )
        started = self.client.post("/tickets/101/run", json={})
        self.assertEqual(started.status_code, 200, started.text)
        invoice = started.json()["pending_actions"][0]
        approved = self.client.post(
            f"/approvals/{invoice['request_id']}/approve",
            json={"approved_by": "Grant", "confirm_exact_request": True, "expected_arguments": invoice["arguments"]},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(self.checking_balance(), 2560.0)
        self.next_proposal = None
        invoice_resume = self.client.post("/tickets/101/run", json={})
        self.assertEqual(invoice_resume.status_code, 200, invoice_resume.text)
        self.assertEqual(invoice_resume.json()["pending_actions"], [])
        self.assertEqual(server.get_ticket_context(101)["ticket"]["status"], "open")
        self.assertTrue(all(call["openai_reasoning_effort"] == "none" for call in self.team_run_kwargs))

        self.set_proposal(
            "record_rent_payment",
            {"lease_id": 1, "account": "checking", "amount": 2400.0},
            "Ticket 102's linked lease.",
        )
        resumed = self.client.post("/tickets/102/run", json={})
        self.assertEqual(resumed.status_code, 200, resumed.text)
        rent = resumed.json()["pending_actions"][0]
        rent_approved = self.client.post(
            f"/approvals/{rent['request_id']}/approve",
            json={"approved_by": "Grant", "confirm_exact_request": True, "expected_arguments": rent["arguments"]},
        )
        self.assertEqual(rent_approved.status_code, 200, rent_approved.text)
        self.assertEqual(self.checking_balance(), 160.0)
        self.next_proposal = None
        rent_resume = self.client.post("/tickets/102/run", json={})
        self.assertEqual(rent_resume.status_code, 200, rent_resume.text)
        self.assertEqual(rent_resume.json()["pending_actions"], [])
        self.assertEqual(server.get_ticket_context(101)["ticket"]["status"], "open")
        self.assertEqual(server.get_ticket_context(102)["ticket"]["status"], "open")

        # Seed the new ledgers on this disposable database, then reset from the
        # untouched original. Reset must restore baseline cash and keep audit history.
        with closing(sqlite3.connect(self.working)) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS purchases (id INTEGER PRIMARY KEY, ticket_id INTEGER, vendor_id INTEGER, sku TEXT, size TEXT, quantity INTEGER, quoted_unit_cost REAL, total_amount REAL, supplier_quote_reference TEXT, account TEXT, status TEXT, ordered_at TEXT, approved_by TEXT, approval_reference TEXT)")
            connection.execute("INSERT INTO purchases (ticket_id, vendor_id, sku, size, quantity, quoted_unit_cost, total_amount, supplier_quote_reference, account, status, ordered_at, approved_by, approval_reference) VALUES (103, 999, 'CC-HOOD-NAVY', 'M', 1, 25.0, 25.0, 'OFFLINE-RESET', 'checking', 'ordered', 'offline', 'Grant', 'offline')")
            connection.commit()
        with closing(sqlite3.connect(self.working)) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS rent_payments (id INTEGER PRIMARY KEY, lease_id INTEGER, due_date TEXT, amount REAL, account TEXT, paid_at TEXT, approved_by TEXT, approval_reference TEXT, UNIQUE(lease_id, due_date))")
            connection.commit()
        response = self.client.post("/database/reset")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.checking_balance(), 3400.0)
        self.assertEqual(hashlib.sha256(self.original.read_bytes()).digest(), hashlib.sha256(self.working.read_bytes()).digest())
        self.assertEqual(self.audit.read_text(encoding="utf-8"), json.dumps(audit_history))
        with closing(sqlite3.connect(self.working)) as connection:
            ledger_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("rent_payments", ledger_tables)
        self.assertNotIn("purchases", ledger_tables)


if __name__ == "__main__":
    unittest.main()
