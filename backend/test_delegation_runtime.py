from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage
from fastapi.testclient import TestClient

from backend import audit, main, team
from backend.models import AgentReply, MCP_WRITE_TOOLS, TeamRequest, TeamRunLimits
from mcp_server import server


class TeamScenario:
    def __init__(self, *, scenario: str):
        self.scenario = scenario
        self.ticket_id = (103 if scenario == "ticket103_full_workflow" else
                          102 if scenario in {"rent_success", "permission_write"} else 101)
        self.boss_calls = 0
        self.facilities_calls = 0
        self.inventory_calls = 0
        self.accounting_calls = 0
        self.self_retry_feedback = False
        self.tool_history: list[tuple[str, dict]] = []
        self.token_count_calls = 0
        self.token_counts: list[int] = []
        self.workflow_product: dict | None = None
        self.workflow_inventory_facts: list[str] = []
        self.workflow_accounting_facts: list[str] = []
        self.workflow_customer_draft: str | None = None
        self.workflow_delegate_results: dict[str, str] = {}

    @staticmethod
    def tool_result(messages, tool_name: str) -> dict:
        for part in reversed(TeamScenario.returns(messages)):
            if getattr(part, "tool_name", None) != tool_name:
                continue
            value = getattr(part, "content", None)
            if isinstance(value, str):
                return json.loads(value)
            if isinstance(value, list):
                return json.loads("".join(
                    getattr(block, "text", "") for block in value
                ))
            if isinstance(value, dict):
                return value
        raise AssertionError(f"No MCP/delegation result for {tool_name}")

    @staticmethod
    def role(info) -> str:
        instructions = info.instructions
        if isinstance(instructions, list):
            instructions = " ".join(str(x) for x in instructions)
        text = str(instructions).casefold()
        for role, cue in (("boss", "you coordinate the shop team"), ("inventory", "you handle stock"),
                          ("accounting", "you handle cash"), ("facilities", "you handle lease and rent"),
                          ("customer_service", "you draft customer replies")):
            if cue in text:
                return role
        raise AssertionError(f"Could not identify agent instructions: {text[:80]}")

    @staticmethod
    def calls(messages):
        return [part for message in messages for part in getattr(message, "parts", ()) if isinstance(part, ToolCallPart)]

    @staticmethod
    def returns(messages):
        return [part for message in messages for part in getattr(message, "parts", ()) if type(part).__name__ == "ToolReturnPart"]

    def final(self, info, role: str, *, pending=None, facts=None):
        output = info.output_tools[0]
        return ModelResponse(parts=[ToolCallPart(output.name, {
            "agent": role, "summary": f"Mocked {role} completed from actual MCP reads.",
            "facts": facts or [], "pending_actions": pending or [], "proposed_updates": [],
        })])

    def __call__(self, messages, info):
        role = self.role(info)
        calls = self.calls(messages)
        returns = self.returns(messages)
        if role == "boss":
            if self.scenario == "ticket103_full_workflow":
                if not any(x.tool_name == "get_ticket_context" for x in calls):
                    return ModelResponse(parts=[ToolCallPart(
                        "get_ticket_context", {"ticket_id": 103})])
                delegation_results = [p for p in returns if p.tool_name == "delegate_to_agent"]
                if len(delegation_results) < 3:
                    target = ("inventory", "accounting", "customer_service")[len(delegation_results)]
                    if target == "inventory":
                        task = "For ticket 103, verify CC-HOOD-NAVY size M stock and pricing. One read only."
                    elif target == "accounting":
                        prior = str(delegation_results[0].content)
                        if "CC-HOOD-NAVY" not in prior:
                            raise AssertionError("Boss did not retain Inventory's validated reply.")
                        task = (f"Use Inventory's verified reply: {prior}. "
                                "Recommend a specific 20-unit discount and margin. No rereads.")
                    else:
                        prior = " ".join(str(part.content) for part in delegation_results[:2])
                        if "$52.20" not in prior or "shortage 12" not in prior:
                            raise AssertionError("Boss did not retain the completed specialist results.")
                        task = (f"Draft a short reply from these verified employee results: {prior}. "
                                "No commitments.")
                    self.workflow_delegate_results[target] = task
                    return ModelResponse(parts=[ToolCallPart(
                        "delegate_to_agent", {"target": target, "task": task})])
                self.workflow_delegate_results.update({
                    name: str(part.content) for name, part in zip(
                        ("inventory", "accounting", "customer_service"),
                        delegation_results, strict=True,
                    )
                })
                if "$52.20" not in self.workflow_delegate_results["accounting"]:
                    raise AssertionError("Accounting's validated recommendation was lost.")
                if "We have 8 navy hoodies" not in self.workflow_delegate_results["customer_service"]:
                    raise AssertionError("Customer Service's validated draft was lost.")
                draft = self.workflow_customer_draft or ""
                return ModelResponse(parts=[ToolCallPart(
                    info.output_tools[0].name,
                    {"agent": "boss",
                     "summary": "Inventory verified stock, Accounting recommended a review-only discount, and Customer Service drafted a reply.",
                     "facts": self.workflow_inventory_facts + self.workflow_accounting_facts,
                     "unresolved_questions": ["Human review of the recommended discount is still required."],
                     "proposed_updates": [draft] if draft else [],
                     "pending_actions": []})])
            if self.scenario == "self_target" and any(
                type(p).__name__ in {"RetryPromptPart", "ModelRetryPromptPart"}
                for m in messages for p in getattr(m, "parts", ())
            ):
                self.self_retry_feedback = True
                return self.final(info, "boss", facts=["Invalid target was rejected and recovered."])
            if self.scenario == "self_target" and self.boss_calls == 0:
                self.boss_calls += 1
                return ModelResponse(parts=[ToolCallPart("delegate_to_agent", {"target": "boss", "task": "try a forbidden self-delegation"})])
            if self.scenario != "boss_limit_with_proposal" and not any(x.tool_name == "get_ticket_context" for x in calls):
                return ModelResponse(parts=[ToolCallPart("get_ticket_context", {"ticket_id": 102})])
            delegates = [x.args_as_dict().get("target") for x in calls if x.tool_name == "delegate_to_agent"]
            if self.scenario == "self_target":
                if any(type(p).__name__ in {"RetryPromptPart", "ModelRetryPromptPart"} for m in messages for p in getattr(m, "parts", ())):
                    return self.final(info, "boss", facts=["Invalid target was rejected and recovered."])
                return self.final(info, "boss")
            if self.scenario == "boss_limit_with_proposal":
                if "inventory" not in delegates:
                    target = "inventory"
                elif "accounting" not in delegates:
                    target = "accounting"
                else:
                    return self.final(info, "boss")
            else:
                target = "inventory" if self.scenario in {"three_reads", "inventory_one_read"} else "facilities"
            if target not in delegates:
                task = "Read product, invoice, and vendor facts for ticket 101." if target == "inventory" else "Verify lease 1 and prepare the rent workflow for ticket 102."
                return ModelResponse(parts=[ToolCallPart("delegate_to_agent", {"target": target, "task": task})])
            return self.final(info, "boss", facts=["Facilities and Accounting replies were received."])

        if role == "facilities":
            if self.scenario == "permission_write":
                if not any(x.tool_name == "record_rent_payment" for x in calls):
                    return ModelResponse(parts=[ToolCallPart("record_rent_payment", {
                        "lease_id": 1, "account": "checking", "amount": 2400.0,
                        "approved_by": "Facilities", "approval_reference": "fake",
                    })])
            if not any(x.tool_name == "get_rent_context" for x in calls):
                return ModelResponse(parts=[ToolCallPart("get_rent_context", {"lease_id": 1})])
            targets = [x.args_as_dict().get("target") for x in calls if x.tool_name == "delegate_to_agent"]
            if "accounting" not in targets:
                return ModelResponse(parts=[ToolCallPart("delegate_to_agent", {"target": "accounting", "task": "Check cash and propose the exact recorded rent payment for ticket 102."})])
            return self.final(info, "facilities", facts=["Lease facts read through MCP."])

        if role == "accounting":
            if self.scenario == "ticket103_full_workflow":
                p = self.workflow_product
                price = float(p["list_price"])
                cost = float(p["unit_cost"])
                discounted = round(price * 0.9, 2)
                margin = round(discounted - cost, 2)
                total = round(discounted * 20, 2)
                self.workflow_accounting_facts = [
                    f"Recommended 10% discount for review: ${discounted:.2f} each, ${total:.2f} total.",
                    f"Recorded internal-cost gross margin: ${margin:.2f} each, ${margin*20:.2f} total ({margin/discounted:.1%}).",
                ]
                return self.final(info, "accounting", facts=self.workflow_accounting_facts)
            if self.scenario == "boss_limit_with_proposal":
                if not any(x.tool_name == "get_invoice_details" for x in calls):
                    return ModelResponse(parts=[ToolCallPart("get_invoice_details", {"invoice_id": 501})])
                if not any(x.tool_name == "get_cash_snapshot" for x in calls):
                    return ModelResponse(parts=[ToolCallPart("get_cash_snapshot", {})])
                proposal = {"tool_name": "record_invoice_payment",
                            "arguments": {"invoice_id": 501, "account": "checking", "amount": 840.0},
                            "reason": "Recorded linked invoice 501 is open and overdue. Checking contains $3,400, enough for $840. Human review required."}
                return self.final(info, "accounting", pending=[proposal])
            if not any(x.tool_name == "get_cash_snapshot" for x in calls):
                return ModelResponse(parts=[ToolCallPart("get_cash_snapshot", {})])
            pending = [{"tool_name": "record_rent_payment", "arguments": {"lease_id": 1, "account": "checking", "amount": 2400.0}, "reason": "Lease 1 records $2,400 rent due 2026-09-02 and checking cash is sufficient. Human approval required."}]
            return self.final(info, "accounting", pending=pending, facts=["Cash was read through MCP."])

        if role == "inventory":
            if self.scenario == "ticket103_full_workflow":
                if not any(x.tool_name == "get_product_snapshot" for x in calls):
                    return ModelResponse(parts=[ToolCallPart(
                        "get_product_snapshot", {"sku": "CC-HOOD-NAVY", "size": "M"})])
                result = self.tool_result(messages, "get_product_snapshot")
                self.workflow_product = result["product"]
                p = self.workflow_product
                self.workflow_inventory_facts = [
                    f"{p['sku']} size {p['size']}: {p['qty']} on hand at {p['location']}; requested 20, shortage {20-int(p['qty'])}.",
                    f"Recorded internal unit cost ${p['unit_cost']:.2f}; list price ${p['list_price']:.2f}.",
                ]
                return self.final(info, "inventory", facts=self.workflow_inventory_facts)
            names = [x.tool_name for x in calls]
            if self.scenario == "boss_limit_with_proposal" and "get_ticket_context" not in names:
                return ModelResponse(parts=[ToolCallPart("get_ticket_context", {"ticket_id": self.ticket_id})])
            if "get_product_snapshot" not in names:
                return ModelResponse(parts=[ToolCallPart("get_product_snapshot", {"sku": "CC-TEE-WHITE", "size": "S"})])
            if self.scenario == "three_reads":
                if "get_invoice_details" not in names:
                    return ModelResponse(parts=[ToolCallPart("get_invoice_details", {"invoice_id": 501})])
                if "get_vendor_details" not in names:
                    return ModelResponse(parts=[ToolCallPart("get_vendor_details", {"vendor_id": 1})])
            return self.final(info, "inventory", facts=["Product stock read through MCP."])

        if self.scenario == "ticket103_full_workflow":
            p = self.workflow_product
            self.workflow_customer_draft = (
                f"We have {p['qty']} navy hoodies in size M available now. "
                "Accounting recommends a 10% bulk discount, subject to your review. "
                "We cannot confirm additional stock or a delivery date yet."
            )
            return ModelResponse(parts=[ToolCallPart(
                info.output_tools[0].name,
                {"agent": "customer_service",
                 "summary": "Drafted a reply from verified stock and pricing facts.",
                 "facts": [], "pending_actions": [],
                 "proposed_updates": [self.workflow_customer_draft]})])
        return self.final(info, "customer_service", facts=["Draft uses verified employee facts."])

class OfflineCountingFunctionModel(FunctionModel):
    def __init__(self, scenario):
        async def response(messages, info):
            result = scenario(messages, info)
            role = scenario.role(info)
            actual_input = (1300 if scenario.scenario == "three_reads" and role == "inventory"
                            else 1300 if scenario.scenario == "ticket103_full_workflow" and role == "boss"
                            else 300 if scenario.scenario == "ticket103_full_workflow" else 800)
            return ModelResponse(parts=result.parts, usage=RequestUsage(input_tokens=actual_input, output_tokens=40))
        super().__init__(response)
        self.scenario = scenario

    async def count_tokens(self, messages, model_settings, model_request_parameters):
        self.scenario.token_count_calls += 1
        count = 1500 if self.scenario.scenario in {"three_reads", "ticket103_full_workflow"} else 500
        self.scenario.token_counts.append(count)
        return RequestUsage(input_tokens=count)


class LivePathOfflineRegression(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hw5-team-livepath-")
        self.root = Path(self.tmp.name)
        self.db = self.root / "working.db"
        shutil.copy2(main.ORIGINAL_DATABASE_PATH, self.db)
        self.old_env = os.environ.get("CAMPUS_CUSTOMS_DB")
        os.environ["CAMPUS_CUSTOMS_DB"] = str(self.db)
        self.old_paths = (main.ORIGINAL_DATABASE_PATH, main.WORKING_DATABASE_PATH,
                          main.APPROVAL_STORE_PATH, main.AUDIT_PATH, server.DATABASE_PATH)
        main.ORIGINAL_DATABASE_PATH = self.db
        main.WORKING_DATABASE_PATH = self.db
        main.APPROVAL_STORE_PATH = self.root / "approvals.sqlite3"
        main.AUDIT_PATH = self.root / "audit.json"
        audit.AUDIT_PATH = self.root / "audit.json"
        server.DATABASE_PATH = self.db
        self.original_run_team = main.run_team
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        (main.ORIGINAL_DATABASE_PATH, main.WORKING_DATABASE_PATH, main.APPROVAL_STORE_PATH,
         main.AUDIT_PATH, server.DATABASE_PATH) = self.old_paths
        audit.AUDIT_PATH = Path(__file__).resolve().parents[1] / "output" / "audit_trail.json"
        main.run_team = self.original_run_team
        if self.old_env is None:
            os.environ.pop("CAMPUS_CUSTOMS_DB", None)
        else:
            os.environ["CAMPUS_CUSTOMS_DB"] = self.old_env
        self.tmp.cleanup()

    def install_scenario(self, scenario: TeamScenario, *, limits=None):
        async def real_team(request, **kwargs):
            with patch("backend.team.load_hw5_credentials", return_value=True):
                return await team.run_team(request, model_override=OfflineCountingFunctionModel(scenario), limits=limits, **kwargs)
        main.run_team = real_team

    def test_boss_facilities_accounting_mcp_proposal_and_usage_via_fastapi(self):
        scenario = TeamScenario(scenario="rent_success")
        self.install_scenario(scenario)
        response = self.client.post("/tickets/102/run", json={})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(len(body["usage"]), 3)
        self.assertEqual(len(body["pending_actions"]), 1)
        self.assertEqual(body["pending_actions"][0]["status"], "pending")
        self.assertEqual(body["pending_actions"][0]["arguments"], {"lease_id": 1, "account": "checking", "amount": 2400.0})
        self.assertEqual({x["agent"] for x in body["usage"]}, {"boss", "facilities", "accounting"})
        self.assertEqual(body["usage_totals"]["requests"], sum(x["requests"] for x in body["usage"]))
        self.assertEqual(body["usage_totals"]["input_tokens"], sum(x["input_tokens"] for x in body["usage"]))
        self.assertGreater(body["usage_totals"]["input_tokens"], 5_000)
        self.assertTrue(all(x["input_tokens"] < 5_000 for x in body["usage"]))
        self.assertEqual(body["usage_totals"]["output_tokens"], sum(x["output_tokens"] for x in body["usage"]))
        self.assertEqual(body["usage_totals"]["requests"], 8)
        history=json.loads((self.root/"audit.json").read_text(encoding="utf-8"))
        complete=[x for x in history if x["step"]=="team_run_completed"]
        self.assertEqual(len(complete),1)
        self.assertEqual(complete[0]["detail"]["usage_totals"],body["usage_totals"])

    def test_facilities_write_permission_failure_has_safe_source_and_usage(self):
        scenario = TeamScenario(scenario="permission_write")
        self.install_scenario(scenario)
        response=self.client.post("/tickets/102/run",json={})
        self.assertEqual(response.status_code,502,response.text)
        detail=response.json()["detail"]
        self.assertEqual(detail["error_type"],"PermissionError")
        self.assertEqual(detail["error_code"],"READ_ONLY_MCP_WRITE_BLOCKED")
        self.assertIn("guard_mcp_write",detail["source_location"])
        self.assertIsNotNone(detail["usage_totals"])

    def test_self_target_is_excluded_and_invalid_tool_call_recovers(self):
        scenario=TeamScenario(scenario="self_target")
        self.install_scenario(scenario)
        with patch("backend.team.load_hw5_credentials",return_value=True):
            agents=team.build_team(model=OfflineCountingFunctionModel(scenario))
        schema=agents["boss"]._function_toolset.tools["delegate_to_agent"].function_schema.json_schema
        self.assertNotIn("boss",schema["properties"]["target"]["enum"])
        response=self.client.post("/tickets/103/run",json={})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(scenario.self_retry_feedback)

    def test_three_read_inventory_path_hits_per_agent_5000_input_guard(self):
        scenario=TeamScenario(scenario="three_reads")
        self.install_scenario(
            scenario, limits=TeamRunLimits(max_input_tokens_per_invocation=5_000)
        )
        response=self.client.post("/tickets/101/run",json={})
        self.assertEqual(response.status_code,502,response.text)
        detail=response.json()["detail"]
        self.assertEqual(detail["error_type"],"UsageLimitExceeded")
        self.assertEqual(detail["error_code"],"AGENT_INPUT_OR_USAGE_LIMIT_EXCEEDED")
        inventory = next(item for item in detail["usage"] if item["agent"] == "inventory")
        self.assertEqual(inventory["requests"], 3)
        self.assertEqual(inventory["tool_calls"], 3)
        self.assertEqual(inventory["input_tokens"], 3900)
        self.assertEqual(inventory["output_tokens"], 120)
        self.assertGreaterEqual(scenario.token_count_calls, 4)
        self.assertEqual(detail["usage_limit"]["scope"], "agent_invocation")
        self.assertEqual(detail["usage_limit"]["limit"], 5_000)
        self.assertGreater(detail["usage_limit"]["observed"], 5_000)
        self.assertEqual(detail["usage_totals"]["requests"], sum(x["requests"] for x in detail["usage"]))
        self.assertEqual(detail["usage_totals"]["input_tokens"], sum(x["input_tokens"] for x in detail["usage"]))

    def test_reduced_inventory_path_completes_after_one_mcp_read(self):
        scenario=TeamScenario(scenario="inventory_one_read")
        self.install_scenario(scenario)
        response=self.client.post("/tickets/101/run",json={})
        self.assertEqual(response.status_code,200,response.text)
        body=response.json()
        self.assertEqual(body["summary"],"Mocked boss completed from actual MCP reads.")
        inventory=next(item for item in body["usage"] if item["agent"]=="inventory")
        self.assertEqual(inventory["tool_calls"],1)
        self.assertEqual(inventory["requests"],2)

    def test_completed_accounting_proposal_is_queued_when_boss_hits_run_limit(self):
        scenario=TeamScenario(scenario="boss_limit_with_proposal")
        self.install_scenario(scenario, limits=TeamRunLimits(max_requests=8))
        response=self.client.post("/tickets/101/run",json={})
        self.assertEqual(response.status_code,502,response.text)
        detail=response.json()["detail"]
        self.assertEqual(detail["usage_totals"]["requests"],8)
        self.assertEqual(sum(item["requests"] for item in detail["usage"]),8)
        for metric in ("tool_calls", "input_tokens", "output_tokens"):
            self.assertEqual(
                detail["usage_totals"][metric],
                sum(item[metric] for item in detail["usage"]),
            )
        self.assertEqual(len({item["agent"] for item in detail["usage"]}), len(detail["usage"]))
        self.assertEqual(len(detail["pending_actions"]),1)
        proposal=detail["pending_actions"][0]
        self.assertEqual(proposal["status"],"pending")
        self.assertTrue(proposal["incomplete_run"])
        self.assertEqual(proposal["source_agent"],"accounting")
        self.assertEqual(proposal["arguments"],{"invoice_id":501,"account":"checking","amount":840.0})
        self.assertEqual(self.client.get("/approvals/pending").json()["requests"],[proposal])
        self.assertEqual(server.get_ticket_context(101)["ticket"]["status"],"open")
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("SELECT balance FROM cash_accounts WHERE name='checking'").fetchone()[0],3400.0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0],0)
        history=json.loads((self.root/"audit.json").read_text(encoding="utf-8"))
        failed=next(e for e in history if e["step"]=="team_run_failed")
        self.assertEqual(failed["detail"]["completed_pending_actions"][0]["source_agent"],"accounting")

    def test_ticket103_full_delegation_mcp_workflow_with_10k_invocation_budget(self):
        scenario=TeamScenario(scenario="ticket103_full_workflow")
        self.install_scenario(scenario)
        response=self.client.post("/tickets/103/run",json={})
        self.assertEqual(response.status_code,200,response.text)
        body=response.json()
        self.assertEqual(body["summary"],
            "Inventory verified stock, Accounting recommended a review-only discount, and Customer Service drafted a reply.")
        self.assertEqual(body["pending_actions"],[])
        self.assertIn("CC-HOOD-NAVY size M: 8 on hand", " ".join(body["facts"]))
        self.assertIn("$52.20 each", " ".join(body["facts"]))
        self.assertEqual(len(body["proposed_updates"]),1)
        self.assertIn("10% bulk discount, subject to your review", body["proposed_updates"][0])
        self.assertIn("cannot confirm additional stock", body["proposed_updates"][0])
        self.assertEqual({item["agent"] for item in body["usage"]},
            {"boss","inventory","accounting","customer_service"})
        self.assertEqual(body["usage_totals"]["requests"],
            sum(item["requests"] for item in body["usage"]))
        self.assertEqual(body["usage_totals"]["requests"],9)
        self.assertLessEqual(body["usage_totals"]["requests"],12)
        self.assertEqual(len(body["usage"]),4)
        boss_usage=next(item for item in body["usage"] if item["agent"]=="boss")
        self.assertGreater(boss_usage["input_tokens"],5_000)
        self.assertLess(boss_usage["input_tokens"],10_000)
        self.assertEqual(body["usage_totals"]["input_tokens"],
            sum(item["input_tokens"] for item in body["usage"]))
        self.assertEqual(body["usage_totals"]["tool_calls"],
            sum(item["tool_calls"] for item in body["usage"]))
        self.assertEqual(body["usage_totals"]["output_tokens"],
            sum(item["output_tokens"] for item in body["usage"]))
        self.assertEqual(scenario.workflow_product["qty"],8)
        self.assertEqual(scenario.workflow_product["list_price"],58)
        self.assertEqual(scenario.workflow_product["unit_cost"],22)
        self.assertEqual(len(scenario.workflow_delegate_results),3)

        events=self.client.get("/events?limit=200").json()["events"]
        completed=[event for event in events if event.get("run_id")==body["run_id"]
                   and event.get("step")=="agent_run_completed"]
        self.assertEqual({event["agent"] for event in completed},
            {"boss","inventory","accounting","customer_service"})
        inventory_event=next(x for x in completed if x["agent"]=="inventory")
        accounting_event=next(x for x in completed if x["agent"]=="accounting")
        customer_event=next(x for x in completed if x["agent"]=="customer_service")
        self.assertIn("shortage 12", " ".join(inventory_event["detail"]["facts"]))
        self.assertIn("$52.20 each", " ".join(accounting_event["detail"]["facts"]))
        self.assertIn("10% bulk discount", " ".join(customer_event["detail"]["proposed_updates"]))
        team_event=next(x for x in events if x.get("run_id")==body["run_id"]
                        and x.get("step")=="team_run_completed")
        self.assertEqual(team_event["detail"]["usage_totals"],body["usage_totals"])
        self.assertEqual(team_event["detail"]["actual_usage"],body["usage"])
        tool_calls=[call for event in events if event.get("run_id")==body["run_id"]
                    for call in event.get("detail",{}).get("tool_calls",[])]
        self.assertEqual(sum(call["tool"]=="delegate_to_agent" for call in tool_calls),3)
        mcp_reads={"get_ticket_context","get_product_snapshot"}
        self.assertEqual(sum(call["tool"] in mcp_reads for call in tool_calls),2,
            [call["tool"] for call in tool_calls])
        self.assertFalse(any(call["tool"] in MCP_WRITE_TOOLS for call in tool_calls))
        self.assertEqual(body["ticket_status"],"open")
        self.assertEqual(server.get_ticket_context(103)["ticket"]["status"],"open")


if __name__ == "__main__":
    unittest.main()
