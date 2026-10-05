"""Bounded read-only smoke run for the inventory agent and ticket 101 data."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys

from .models import TeamRequest, TeamRunLimits
from .team import PROJECT_ROOT, run_team


SMOKE_PROMPT = (
    "For ticket 101, use the MCP get_product_snapshot tool with "
    "sku='CC-TEE-WHITE' and size='S'. After that single read, use its returned "
    "MCP result in the conversation to return a valid AgentReply with only the "
    "verified stock quantity, "
    "location, list price, and ticket ID. Do not delegate, call a write tool, "
    "change data, resolve the ticket, or make any customer promise."
)


def _database_hash() -> str:
    database = PROJECT_ROOT / "data" / "campus_customs_new.db"
    return hashlib.sha256(database.read_bytes()).hexdigest()


def _audit_records() -> list[dict[str, object]]:
    audit_path = PROJECT_ROOT / "output" / "audit_trail.json"
    if not audit_path.exists():
        return []
    history = json.loads(audit_path.read_text(encoding="utf-8"))
    if not isinstance(history, list):
        raise ValueError("Existing audit trail must be a JSON array. It was left untouched.")
    return history


async def main() -> None:
    before = _database_hash()
    audit_before = len(_audit_records())
    try:
        result = await run_team(
            TeamRequest(task=SMOKE_PROMPT, ticket_id=101),
            entry_agent="inventory",
            limits=TeamRunLimits(
                max_requests=3,
                max_delegations=0,
                max_delegation_depth=1,
                max_mcp_tool_calls=1,
                max_input_chars=1_000,
                max_input_tokens_per_invocation=5_000,
                max_output_tokens_per_request=1_500,
                max_output_tokens_per_run=3_000,
            ),
            read_only=True,
            openai_reasoning_effort="none",
            capture_openai_response_details=True,
        )
    except Exception as exc:
        after = _database_hash()
        records = _audit_records()
        appended = records[audit_before:]
        failure_usage = [
            item.get("detail", {}).get("actual_usage")
            for item in appended
            if item.get("step") == "agent_run_failed"
            and isinstance(item.get("detail"), dict)
            and item.get("detail", {}).get("actual_usage")
        ]
        failure_diagnostics = [
            {
                "step": item.get("step"),
                "node_type": item.get("node_type"),
                "detail": item.get("detail", {}),
            }
            for item in appended
            if item.get("step") in {"model_response_observed", "agent_run_failed"}
        ]
        print(
            json.dumps(
                {
                    "agent": "inventory",
                    "provider": "OpenAI API directly",
                    "model": "gpt-6-luna",
                    "prompt": SMOKE_PROMPT,
                    "test_passed": False,
                    "error_type": type(exc).__name__,
                    "failure_diagnostics": failure_diagnostics,
                    "actual_usage": failure_usage,
                    "database_hash_before": before,
                    "database_hash_after": after,
                    "database_unchanged": before == after,
                    "audit_entries_before": audit_before,
                    "audit_entries_after": len(records),
                    "audit_entries_appended": len(records) - audit_before,
                },
                indent=2,
            )
        )
        raise SystemExit(1) from None

    after = _database_hash()
    audit_after = len(_audit_records())
    if before != after:
        raise RuntimeError("Working database hash changed during read-only smoke run.")
    if audit_after <= audit_before:
        raise RuntimeError("No real audit steps were appended during the smoke run.")
    print(
        json.dumps(
            {
                "agent": "inventory",
                "provider": "OpenAI API directly",
                "model": "gpt-6-luna",
                "prompt": SMOKE_PROMPT,
                "test_passed": True,
                "result": result.result.model_dump(mode="json"),
                "actual_usage": [usage.model_dump(mode="json") for usage in result.usage],
                "total_requests": sum(usage.requests for usage in result.usage),
                "total_mcp_tool_calls": sum(usage.tool_calls for usage in result.usage),
                "total_input_tokens": sum(usage.input_tokens for usage in result.usage),
                "total_output_tokens": sum(usage.output_tokens for usage in result.usage),
                "total_tokens": sum(
                    usage.input_tokens + usage.output_tokens for usage in result.usage
                ),
                "database_hash_before": before,
                "database_hash_after": after,
                "database_unchanged": True,
                "audit_entries_before": audit_before,
                "audit_entries_after": audit_after,
                "audit_entries_appended": audit_after - audit_before,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
