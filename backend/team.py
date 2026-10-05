"""Five-agent PydanticAI team with mutual delegation and MCP-only shop access."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import sys
import traceback
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from pydantic_ai import Agent, ModelRetry, RunContext, UsageLimits
from pydantic_ai.mcp import MCPToolset
from dotenv import load_dotenv

from .audit import append_audit_step
from .models import (
    AgentName,
    AgentReply,
    AgentUsage,
    ApprovedToolAction,
    PendingActionProposal,
    MCP_WRITE_TOOLS,
    consume_approved_action,
    TeamRequest,
    TeamRunLimits,
    TeamRunReport,
)
from .openai_responses import AuditedOpenAIResponsesModel


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHARED_ENV_PATH = PROJECT_ROOT.parent / ".env"
PROMPT_DIR = PROJECT_ROOT / "backend" / "prompts"
MCP_SERVER_PATH = PROJECT_ROOT / "mcp_server" / "server.py"
MODEL_NAME = "openai:gpt-6-luna"
OPENAI_REASONING_EFFORT = "none"
_ROLES: tuple[AgentName, ...] = (
    "boss",
    "inventory",
    "accounting",
    "facilities",
    "customer_service",
)


def safe_error_diagnostics(exc: BaseException, *, read_only: bool = False) -> dict[str, Any]:
    """Return a stable error code and source location without exception text."""
    frames = traceback.extract_tb(exc.__traceback__)
    last = frames[-1] if frames else None
    source = (
        f"{Path(last.filename).name}:{last.name}:{last.lineno}"
        if last else "unknown"
    )
    if isinstance(exc, PermissionError):
        code = "READ_ONLY_MCP_WRITE_BLOCKED" if read_only else "EXACT_APPROVAL_REQUIRED"
    elif isinstance(exc, ValueError) and "delegate" in str(exc).casefold():
        code = "DELEGATION_TARGET_INVALID"
    elif type(exc).__name__ == "UsageLimitExceeded":
        code = "AGENT_INPUT_OR_USAGE_LIMIT_EXCEEDED"
    elif isinstance(exc, ValueError):
        code = "INVALID_AGENT_INPUT_OR_OUTPUT"
    else:
        code = "AGENT_EXECUTION_FAILED"
    result: dict[str, Any] = {"error_code": code, "source_location": source}
    if type(exc).__name__ == "UsageLimitExceeded":
        # PydanticAI's message contains numeric limits and counts here. Do not
        # persist prompts, keys, provider bodies, or raw reasoning.
        match = re.search(
            r"input_tokens_limit of (\d+) \(input_tokens=(\d+)\)", str(exc)
        )
        if match:
            result["usage_limit"] = {
                "scope": "agent_invocation",
                "limit": int(match.group(1)),
                "observed": int(match.group(2)),
            }
    return result


def available_delegates_for(role: str) -> tuple[str, ...]:
    return tuple(candidate for candidate in _ROLES if candidate != role)


def load_hw5_credentials() -> bool:
    """Load a clone's local key or the established shared key into this process."""
    load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False, encoding="utf-8")
    load_dotenv(dotenv_path=SHARED_ENV_PATH, override=False, encoding="utf-8")
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


@dataclass
class TeamDeps:
    """State shared by one top-level run and all its delegated agents."""

    agents: dict[str, Agent[Any, AgentReply]]
    mcp_toolset: MCPToolset
    run_id: str
    ticket_id: int | None
    limits: TeamRunLimits = field(default_factory=TeamRunLimits)
    requests_used: int = 0
    delegations_used: int = 0
    delegation_depth: int = 0
    model_requests_used: int = 0
    mcp_tool_calls_used: int = 0
    read_only_mode: bool = False
    usage_records: list[AgentUsage] = field(default_factory=list)
    approved_actions: list[ApprovedToolAction] = field(default_factory=list)
    pending_action_proposals: list[PendingActionProposal] = field(default_factory=list)
    pending_action_candidates: list[dict[str, Any]] = field(default_factory=list)
    model_override: Any | None = None
    openai_reasoning_effort: str = OPENAI_REASONING_EFFORT

    def usage_limits_for_invocation(self) -> UsageLimits:
        """Create independent PydanticAI limits for each AgentRun."""
        return UsageLimits(
            request_limit=None,
            tool_calls_limit=None,
            input_tokens_limit=self.limits.max_input_tokens_per_invocation,
            output_tokens_limit=self.limits.max_output_tokens_per_run,
            count_tokens_before_request=True,
        )


def build_team(model: Any | None = None) -> dict[str, Agent[TeamDeps, AgentReply]]:
    """Construct agents with the same explicit direct-OpenAI model.

    PydanticAI reads OPENAI_API_KEY from the environment when a run starts.
    This factory does not inspect or use keys for any other provider.
    """
    if not load_hw5_credentials():
        raise RuntimeError(
            "OPENAI_API_KEY is missing from the process, project .env, and shared .env."
        )

    agents: dict[str, Agent[TeamDeps, AgentReply]] = {}
    for role in _ROLES:
        prompt_path = PROMPT_DIR / f"{role}.md"

        def read_prompt(path: Path):
            def instructions(_ctx: RunContext[TeamDeps]) -> str:
                shared_rules = (
                    "Use verified facts already supplied by Boss or another employee. "
                    "Do not reread the ticket or repeat an MCP read unless a needed fact is missing. "
                    "During a read-only ticket run, never call any MCP write tool. Put exact financial "
                    "requests in AgentReply.pending_actions for the human approval desk."
                )
                return shared_rules + "\n\n" + path.read_text(encoding="utf-8")

            return instructions

        agent = Agent(
            model or MODEL_NAME,
            name=role,
            output_type=AgentReply,
            deps_type=TeamDeps,
            instructions=read_prompt(prompt_path),
            retries=1,
            tool_timeout=30,
        )
        agents[role] = agent

    for source_role, agent in agents.items():

        def make_delegate(role: AgentName):
            async def delegate_to_agent(
                ctx: RunContext[TeamDeps],
                target: str,
                task: str,
            ) -> AgentReply:
                """Delegate a bounded task to any of the other four agents."""
                deps = ctx.deps
                available = available_delegates_for(role)
                if target not in available:
                    raise ModelRetry(
                        f"Invalid delegation target. Choose one of: {', '.join(available)}."
                    )
                if len(task) > deps.limits.max_input_chars:
                    raise ValueError("Delegated task exceeds the configured character limit.")
                if deps.delegation_depth >= deps.limits.max_delegation_depth:
                    raise ValueError("Maximum delegation depth reached.")
                if deps.delegations_used >= deps.limits.max_delegations:
                    raise ValueError("Maximum delegation count reached.")
                if deps.requests_used >= deps.limits.max_agent_invocations:
                    raise ValueError("Maximum agent invocation count reached.")

                deps.delegations_used += 1
                deps.delegation_depth += 1
                append_audit_step(
                    run_id=deps.run_id,
                    agent=role,
                    ticket_id=deps.ticket_id,
                    step="delegation_started",
                    node_type="agent_tool_call",
                    detail={"target": target, "task_chars": len(task)},
                )
                try:
                    result = await _run_agent(deps.agents[target], target, task, deps)
                    append_audit_step(
                        run_id=deps.run_id,
                        agent=role,
                        ticket_id=deps.ticket_id,
                        step="delegation_completed",
                        node_type="agent_tool_result",
                        detail={"target": target},
                    )
                    return result
                finally:
                    deps.delegation_depth -= 1

            delegate_to_agent.__annotations__["target"] = Literal[available_delegates_for(role)]
            return delegate_to_agent

        agent.tool(make_delegate(source_role))

    return agents


def model_settings_for_agent(
    limits: TeamRunLimits,
    reasoning_effort: str = OPENAI_REASONING_EFFORT,
) -> dict[str, object]:
    """Settings shared by top-level and delegated agents on every model request."""
    return {
        "max_tokens": limits.max_output_tokens_per_request,
        "openai_reasoning_effort": reasoning_effort,
    }


async def _run_agent(
    agent: Agent[TeamDeps, AgentReply],
    role: AgentName,
    prompt: str,
    deps: TeamDeps,
) -> AgentReply:
    if not prompt or len(prompt) > deps.limits.max_input_chars:
        raise ValueError("Agent input is empty or exceeds the configured character limit.")
    if deps.requests_used >= deps.limits.max_agent_invocations:
        raise ValueError("Maximum agent invocation count reached.")
    deps.requests_used += 1
    agent_run = None
    usage_recorded = False
    try:
        async with agent.iter(
            prompt,
            model=deps.model_override,
            deps=deps,
            usage_limits=deps.usage_limits_for_invocation(),
            toolsets=[deps.mcp_toolset],
            model_settings=model_settings_for_agent(
                deps.limits, deps.openai_reasoning_effort
            ),
        ) as agent_run:
            node = agent_run.next_node
            while type(node).__name__ != "End":
                if type(node).__name__ == "ModelRequestNode":
                    if deps.model_requests_used >= deps.limits.max_requests:
                        raise RuntimeError("Maximum model request count reached for this team run.")
                    deps.model_requests_used += 1
                node = await agent_run.next(node)
                detail: dict[str, Any] = {}
                if type(node).__name__ == "ModelRequestNode":
                    retry_summaries = []
                    for part in getattr(node.request, "parts", ()):
                        if type(part).__name__ not in {"RetryPromptPart", "ModelRetryPromptPart"}:
                            continue
                        content = getattr(part, "content", None)
                        content_text = content if isinstance(content, str) else ""
                        lowered = content_text.lower()
                        if "validation error" in lowered or "invalid" in lowered:
                            reason = "structured_output_validation_or_invalid_tool_arguments"
                        elif "no tool call" in lowered or "no response" in lowered:
                            reason = "missing_actionable_output"
                        else:
                            reason = "model_retry_prompt"
                        retry_summaries.append(
                            {
                                "part_type": type(part).__name__,
                                "tool_name": getattr(part, "tool_name", None),
                                "reason": reason,
                                "content_chars": len(content_text),
                                "content_sha256": hashlib.sha256(content_text.encode()).hexdigest(),
                            }
                        )
                    if retry_summaries:
                        detail["retry_reasons"] = retry_summaries
                if type(node).__name__ == "CallToolsNode":
                    safe_keys = {
                        "target", "ticket_id", "invoice_id", "lease_id", "sku",
                        "size", "quantity", "status", "amount", "account",
                    }
                    tool_calls = []
                    for part in node.model_response.parts:
                        if type(part).__name__ != "ToolCallPart":
                            continue
                        raw_args = part.args_as_dict()
                        tool_calls.append(
                            {
                                "tool": part.tool_name,
                                "arguments": {
                                    key: value
                                    for key, value in raw_args.items()
                                    if key in safe_keys
                                },
                            }
                        )
                    detail["tool_calls"] = tool_calls
                    response = node.model_response
                    safe_provider_details = response.provider_details or {}
                    detail["response"] = {
                        "finish_reason": getattr(response, "finish_reason", None),
                        "provider_status": safe_provider_details.get("openai_response_status"),
                        "incomplete_details": safe_provider_details.get("openai_incomplete_details"),
                        "part_types": dict(Counter(type(part).__name__ for part in response.parts)),
                        "text_chars": sum(
                            len(part.content)
                            for part in response.parts
                            if type(part).__name__ == "TextPart"
                        ),
                    }
                    retry_parts = []
                    for message in agent_run.all_messages():
                        for part in getattr(message, "parts", ()):
                            if type(part).__name__ in {"RetryPromptPart", "ModelRetryPromptPart"}:
                                content = getattr(part, "content", None)
                                retry_parts.append({
                                    "part_type": type(part).__name__,
                                    "tool_name": getattr(part, "tool_name", None),
                                    "content_type": type(content).__name__,
                                    "content_chars": len(content) if isinstance(content, str) else None,
                                    "content_sha256": hashlib.sha256(content.encode()).hexdigest() if isinstance(content, str) else None,
                                })
                    if retry_parts:
                        detail["retry_prompts"] = retry_parts
                    append_audit_step(
                        run_id=deps.run_id,
                        agent=role,
                        ticket_id=deps.ticket_id,
                        step="model_response_observed",
                        node_type="CallToolsNode",
                        detail={key: detail[key] for key in ("response", "retry_prompts") if key in detail},
                    )
                append_audit_step(
                    run_id=deps.run_id,
                    agent=role,
                    ticket_id=deps.ticket_id,
                    step="agent_loop_step",
                    node_type=type(node).__name__,
                    detail=detail,
                )
            if agent_run.result is None:
                raise RuntimeError(f"Agent {role} finished without a result.")
            if not isinstance(agent_run.result.output, AgentReply):
                raise TypeError(f"Agent {role} produced an unvalidated output type.")
            deps.pending_action_proposals.extend(agent_run.result.output.pending_actions)
            deps.pending_action_candidates.extend(
                {
                    "source_agent": role,
                    "proposal": proposal.model_dump(mode="json"),
                }
                for proposal in agent_run.result.output.pending_actions
            )
            usage = agent_run.usage
            deps.usage_records.append(
                AgentUsage(
                    agent=role,
                    requests=usage.requests,
                    tool_calls=usage.tool_calls,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    details=dict(usage.details),
                )
            )
            usage_recorded = True
            append_audit_step(
                run_id=deps.run_id,
                agent=role,
                ticket_id=deps.ticket_id,
                step="agent_run_completed",
                node_type="result",
                detail={
                    "output_type": type(agent_run.result.output).__name__,
                    "structured_output_valid": isinstance(agent_run.result.output, AgentReply),
                    "completion_reason": "AgentReply validated",
                    "summary": agent_run.result.output.summary,
                    "facts": agent_run.result.output.facts,
                    "unresolved_questions": agent_run.result.output.unresolved_questions,
                    "proposed_updates": agent_run.result.output.proposed_updates,
                    "pending_actions": [
                        proposal.model_dump(mode="json")
                        for proposal in agent_run.result.output.pending_actions
                    ],
                    "actual_usage": deps.usage_records[-1].model_dump(mode="json"),
                },
            )
            return agent_run.result.output
    except Exception as exc:
        failure_detail: dict[str, Any] = {}
        if agent_run is not None:
            usage = agent_run.usage
            usage_record = AgentUsage(
                agent=role,
                requests=usage.requests,
                tool_calls=usage.tool_calls,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                details=dict(usage.details),
            )
            failure_detail["actual_usage"] = usage_record.model_dump(mode="json")
            if not usage_recorded:
                deps.usage_records.append(usage_record)
        validation_errors = getattr(exc, "errors", None)
        if callable(validation_errors):
            try:
                failure_detail["structured_output_validation"] = [
                    {
                        "type": item.get("type"),
                        "location": [str(part) for part in item.get("loc", ())],
                    }
                    for item in validation_errors(include_url=False)
                ]
            except Exception:
                failure_detail["structured_output_validation"] = {"inspection": "unavailable"}
        diagnostics = safe_error_diagnostics(exc, read_only=deps.read_only_mode)
        failure_detail.update(diagnostics)
        for key, value in diagnostics.items():
            setattr(exc, key, value)
        failure_detail["completed_pending_actions"] = list(deps.pending_action_candidates)
        failure_detail["retry_reason"] = {
            "exception_type": type(exc).__name__,
            "message_chars": len(str(exc)),
            "message_sha256": hashlib.sha256(str(exc).encode()).hexdigest(),
        }
        append_audit_step(
            run_id=deps.run_id,
            agent=role,
            ticket_id=deps.ticket_id,
            step="agent_run_failed",
            node_type=type(exc).__name__,
            detail=failure_detail,
        )
        raise


async def run_team(
    request: TeamRequest,
    *,
    entry_agent: AgentName = "boss",
    limits: TeamRunLimits | None = None,
    read_only: bool = False,
    approved_actions: list[ApprovedToolAction] | None = None,
    openai_reasoning_effort: str = OPENAI_REASONING_EFFORT,
    capture_openai_response_details: bool = False,
    model_override: Any | None = None,
) -> TeamRunReport:
    """Run the Boss entry point. Writes require exact caller-approved actions."""
    run_limits = limits or TeamRunLimits()
    if len(request.task) > run_limits.max_input_chars:
        raise ValueError("Request exceeds the configured character limit.")
    if not load_hw5_credentials():
        raise RuntimeError(
            f"OPENAI_API_KEY is required for the selected direct OpenAI provider. "
            f"It was not found in the process or {SHARED_ENV_PATH}. "
            "No alternate provider or model is configured."
        )
    if model_override is None and capture_openai_response_details:
        model_override = AuditedOpenAIResponsesModel("gpt-6-luna")
    if (openai_reasoning_effort == "none"
            and isinstance(model_override, AuditedOpenAIResponsesModel)):
        if not model_override.profile.get("openai_supports_reasoning_effort_none", False):
            raise RuntimeError("Installed OpenAI model profile does not support reasoning effort 'none'.")
    agents = build_team(model_override)
    mcp_client = Client(
        StdioTransport(
            command=sys.executable,
            args=[str(MCP_SERVER_PATH)],
            env={"CAMPUS_CUSTOMS_DB": os.environ.get(
                "CAMPUS_CUSTOMS_DB",
                str(PROJECT_ROOT / "data" / "campus_customs_new.db"),
            )},
            cwd=str(PROJECT_ROOT),
        ),
        name="campus_customs_team",
    )
    async def guard_mcp_write(ctx, call_tool, tool_name: str, arguments: dict[str, Any]):
        if ctx.deps.mcp_tool_calls_used >= ctx.deps.limits.max_mcp_tool_calls:
            raise RuntimeError("Maximum MCP tool call count reached for this team run.")
        ctx.deps.mcp_tool_calls_used += 1
        if tool_name in MCP_WRITE_TOOLS:
            if ctx.deps.read_only_mode:
                raise PermissionError("This run is read-only. MCP write calls are disabled.")
            consume_approved_action(ctx.deps.approved_actions, tool_name, arguments)
        return await call_tool(tool_name, arguments)

    mcp_toolset = MCPToolset(
        mcp_client, id="campus_customs", process_tool_call=guard_mcp_write
    )
    run_id = str(uuid4())
    deps = TeamDeps(
        agents=agents,
        mcp_toolset=mcp_toolset,
        run_id=run_id,
        ticket_id=request.ticket_id,
        limits=run_limits,
        read_only_mode=read_only,
        approved_actions=list(approved_actions or []),
        model_override=model_override,
        openai_reasoning_effort=openai_reasoning_effort,
    )
    append_audit_step(
        run_id=run_id,
        agent=entry_agent,
        ticket_id=request.ticket_id,
        step="team_run_started",
        node_type="request",
        detail={"task_chars": len(request.task)},
    )
    try:
        async with mcp_client:
            result = await _run_agent(agents[entry_agent], entry_agent, request.task, deps)
    except Exception as exc:
        failed_usage = [item.model_dump(mode="json") for item in deps.usage_records]
        failed_totals = {
            "requests": sum(item.requests for item in deps.usage_records),
            "tool_calls": sum(item.tool_calls for item in deps.usage_records),
            "input_tokens": sum(item.input_tokens for item in deps.usage_records),
            "output_tokens": sum(item.output_tokens for item in deps.usage_records),
        }
        diagnostics = safe_error_diagnostics(exc, read_only=read_only)
        append_audit_step(
            run_id=run_id, agent=entry_agent, ticket_id=request.ticket_id,
            step="team_run_failed", node_type=type(exc).__name__,
            detail={"error_type": type(exc).__name__, **diagnostics,
                    "actual_usage": failed_usage, "usage_totals": failed_totals,
                    "completed_pending_actions": list(deps.pending_action_candidates)},
        )
        setattr(exc, "error_code", diagnostics["error_code"])
        setattr(exc, "source_location", diagnostics["source_location"])
        for key, value in diagnostics.items():
            setattr(exc, key, value)
        setattr(exc, "actual_usage", failed_usage)
        setattr(exc, "usage_totals", failed_totals)
        setattr(exc, "run_id", run_id)
        setattr(exc, "validated_pending_actions", list(deps.pending_action_candidates))
        raise
    usage_totals = {
        "requests": sum(item.requests for item in deps.usage_records),
        "tool_calls": sum(item.tool_calls for item in deps.usage_records),
        "input_tokens": sum(item.input_tokens for item in deps.usage_records),
        "output_tokens": sum(item.output_tokens for item in deps.usage_records),
    }
    append_audit_step(
        run_id=run_id,
        agent=entry_agent,
        ticket_id=request.ticket_id,
                step="team_run_completed",
                node_type="result",
        detail={
            "summary_chars": len(result.summary),
            "structured_output_valid": isinstance(result, AgentReply),
            "completion_reason": "AgentReply validated",
            "actual_usage": [item.model_dump(mode="json") for item in deps.usage_records],
            "usage_totals": usage_totals,
        },
    )
    return TeamRunReport(
        run_id=run_id,
        result=result,
        usage=deps.usage_records,
        pending_actions=deps.pending_action_proposals,
        pending_action_candidates=deps.pending_action_candidates,
    )


def validate_team_wiring() -> dict[str, Any]:
    """Static wiring checks that do not call an LLM or alter the database."""
    prompt_files = {role: (PROMPT_DIR / f"{role}.md").is_file() for role in _ROLES}
    return {
        "model": MODEL_NAME,
        "provider": "OpenAI API directly",
        "openai_key_present": load_hw5_credentials(),
        "credential_source": str(SHARED_ENV_PATH),
        "roles": list(_ROLES),
        "prompt_files_exist": prompt_files,
        "mcp_server_exists": MCP_SERVER_PATH.is_file(),
        "database_exists": (PROJECT_ROOT / "data" / "campus_customs_new.db").is_file(),
    }
