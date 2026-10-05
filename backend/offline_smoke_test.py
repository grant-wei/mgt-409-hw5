"""Explicitly mocked offline checks for smoke request and MCP handling."""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace
from uuid import uuid4

import httpx2 as httpx
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from openai import AsyncOpenAI
from pydantic_ai import Agent, UsageLimits
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.providers.openai import OpenAIProvider

from .audit import append_audit_step
from .models import AgentReply, MCP_WRITE_TOOLS, TeamRunLimits
from .openai_responses import AuditedOpenAIResponsesModel
from .team import MCP_SERVER_PATH, PROJECT_ROOT, TeamDeps, _run_agent


async def check_openai_request_settings() -> None:
    captured: dict[str, object] = {}

    async def mock_transport(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            400,
            json={"error": {"message": "offline request capture", "type": "invalid_request_error"}},
            request=request,
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_transport))
    sdk_client = AsyncOpenAI(api_key="offline-mock-key", http_client=http_client, max_retries=0)
    model = AuditedOpenAIResponsesModel(
        "gpt-6-luna",
        provider=OpenAIProvider(openai_client=sdk_client),
    )
    assert model.profile.get("openai_supports_reasoning_effort_none") is True
    provider_details = model._process_provider_details(
        SimpleNamespace(
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="mocked_reason"),
        )
    )
    assert provider_details == {
        "openai_response_status": "incomplete",
        "openai_incomplete_details": {"reason": "mocked_reason"},
    }
    agent = Agent(model, output_type=AgentReply)
    try:
        await agent.run(
            "Offline request-setting check.",
            model_settings={
                "openai_reasoning_effort": "none",
                "max_tokens": 1_500,
            },
        )
    except Exception:
        pass
    finally:
        await sdk_client.close()

    body = captured.get("body")
    assert captured.get("path") == "/v1/responses", captured.get("path")
    assert isinstance(body, dict)
    assert body.get("model") == "gpt-6-luna"
    assert body.get("reasoning", {}).get("effort") == "none"
    assert body.get("max_output_tokens") == 1_500
    append_audit_step(
        run_id=str(uuid4()),
        agent="inventory",
        ticket_id=101,
        step="mocked_request_settings_check_passed",
        node_type="offline_http_mock",
        detail={
            "run_kind": "MOCKED_OFFLINE",
            "live_provider_requests": 0,
            "mock_openai_http_requests": 1,
            "captured_endpoint": captured["path"],
            "model": body["model"],
            "reasoning_effort": body["reasoning"]["effort"],
            "max_output_tokens": body["max_output_tokens"],
            "api_key_logged": False,
            "raw_reasoning_logged": False,
            "response_status_logging": provider_details["openai_response_status"],
            "incomplete_details_logging": provider_details["openai_incomplete_details"],
        },
    )


async def check_mcp_result_and_structured_reply() -> AgentReply:
    limits = TeamRunLimits(
        max_requests=2,
        max_delegations=0,
        max_delegation_depth=1,
        max_mcp_tool_calls=1,
        max_input_chars=1_000,
        max_input_tokens_per_invocation=5_000,
        max_output_tokens_per_request=1_500,
        max_output_tokens_per_run=3_000,
    )
    mock_requests = 0
    result_was_preserved = False

    def mock_response(messages, info):
        nonlocal mock_requests, result_was_preserved
        mock_requests += 1
        if mock_requests == 1:
            return ModelResponse(
                parts=[ToolCallPart("get_product_snapshot", {"sku": "CC-TEE-WHITE", "size": "S"})]
            )
        returned = [
            part
            for message in messages
            for part in getattr(message, "parts", ())
            if type(part).__name__ == "ToolReturnPart"
            and getattr(part, "tool_name", None) == "get_product_snapshot"
        ]
        assert len(returned) == 1, "MCP tool result was missing from the second model context."
        result_content = returned[0].content
        if isinstance(result_content, str):
            result_content = json.loads(result_content)
        if isinstance(result_content, list):
            chunks = [getattr(block, "text", None) for block in result_content]
            result_content = json.loads("".join(chunk for chunk in chunks if isinstance(chunk, str)))
        product = result_content.get("product") if isinstance(result_content, dict) else None
        assert isinstance(product, dict), f"Unexpected MCP result shape: {type(result_content).__name__}"
        result_was_preserved = True
        output_tool = info.output_tools[0]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "agent": "inventory",
                        "summary": f"Ticket 101 verified {product['sku']} stock and price from MCP.",
                        "facts": [
                            f"SKU {product['sku']}, size {product['size']}, quantity {product['qty']}, location {product['location']}, list price {product['list_price']}."
                        ],
                        "unresolved_questions": [],
                        "proposed_updates": [],
                    },
                )
            ]
        )

    mock_model = FunctionModel(mock_response, model_name="offline-mock")
    agent = Agent(
        mock_model,
        name="inventory_mocked_offline_test",
        output_type=AgentReply,
        deps_type=TeamDeps,
        instructions=(PROJECT_ROOT / "backend" / "prompts" / "inventory.md").read_text(encoding="utf-8"),
        retries=1,
    )
    client = Client(
        StdioTransport(command=sys.executable, args=[str(MCP_SERVER_PATH)], cwd=str(PROJECT_ROOT)),
        name="campus_customs_mocked_offline_test",
    )
    calls = 0

    async def read_only_guard(ctx, call_tool, tool_name, arguments):
        nonlocal calls
        if tool_name in MCP_WRITE_TOOLS:
            raise PermissionError("Mocked offline test disables all writes.")
        calls += 1
        if calls > limits.max_mcp_tool_calls:
            raise RuntimeError("Mocked offline MCP read limit exceeded.")
        return await call_tool(tool_name, arguments)

    run_id = str(uuid4())
    toolset = MCPToolset(client, id="campus_customs_mocked_offline", process_tool_call=read_only_guard)
    deps = TeamDeps(
        agents={"inventory": agent},
        mcp_toolset=toolset,
        run_id=run_id,
        ticket_id=101,
        limits=limits,
        read_only_mode=True,
        model_override=mock_model,
    )
    deps.usage_limits = UsageLimits(
        request_limit=limits.max_requests,
        tool_calls_limit=limits.max_mcp_tool_calls,
        input_tokens_limit=limits.max_input_tokens_per_invocation,
        output_tokens_limit=limits.max_output_tokens_per_run,
        count_tokens_before_request=False,
    )
    append_audit_step(
        run_id=run_id,
        agent="inventory",
        ticket_id=101,
        step="mocked_mcp_agent_test_started",
        node_type="offline_mock",
        detail={"run_kind": "MOCKED_OFFLINE", "live_provider_requests": 0, "writes_enabled": False},
    )
    async with client:
        result = await _run_agent(
            agent,
            "inventory",
            "For ticket 101, read CC-TEE-WHITE size S once through MCP. Use that result to return a valid AgentReply.",
            deps,
        )
    assert isinstance(result, AgentReply)
    assert result.agent == "inventory"
    assert result_was_preserved
    assert calls == 1, f"Expected exactly one MCP read, got {calls}."
    assert mock_requests == 2
    append_audit_step(
        run_id=run_id,
        agent="inventory",
        ticket_id=101,
        step="mocked_mcp_agent_test_completed",
        node_type="offline_mock",
        detail={
            "run_kind": "MOCKED_OFFLINE",
            "live_provider_requests": 0,
            "mock_model_requests": mock_requests,
            "mcp_reads": calls,
            "tool_result_preserved_in_model_context": result_was_preserved,
            "agent_reply_valid": True,
            "writes_enabled": False,
        },
    )
    return result


async def main() -> None:
    await check_openai_request_settings()
    result = await check_mcp_result_and_structured_reply()
    print(
        {
            "passed": True,
            "run_kind": "MOCKED_OFFLINE",
            "request_settings_check": "passed: actual mocked POST /v1/responses has reasoning.effort=none and max_output_tokens=1500",
            "tool_result_preserved_in_model_context": True,
            "live_provider_requests": 0,
            "mock_model_requests": 2,
            "mcp_reads": 1,
            "writes_enabled": False,
            "agent_reply": result.model_dump(mode="json"),
        }
    )


if __name__ == "__main__":
    asyncio.run(main())
