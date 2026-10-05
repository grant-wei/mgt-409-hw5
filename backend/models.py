"""Shared input and result types for the Campus Customs agent team."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


AgentName = Literal["boss", "inventory", "accounting", "facilities", "customer_service"]
TicketStatus = Literal["open", "pending", "resolved"]
MCP_WRITE_TOOLS = frozenset({
    "record_invoice_payment",
    "record_rent_payment",
    "record_purchase_order",
    "set_inventory_quantity",
    "record_inventory_receipt",
    "advance_simulated_shop_date",
    "update_ticket_status",
})


class PendingActionProposal(BaseModel):
    """Structured, unapproved request for a financial MCP write."""

    tool_name: Literal[
        "record_invoice_payment", "record_rent_payment", "record_purchase_order"
    ]
    arguments: dict[str, object]
    reason: str = Field(min_length=1, max_length=1_000)


class AgentTask(BaseModel):
    """A bounded task passed between agents."""

    task: str = Field(min_length=1, max_length=2_000)
    ticket_id: int | None = None


class AgentReply(BaseModel):
    """A concise, evidence-aware result returned by an agent."""

    agent: AgentName
    summary: str = Field(max_length=4_000)
    facts: list[str] = Field(default_factory=list, max_length=20)
    unresolved_questions: list[str] = Field(default_factory=list, max_length=10)
    proposed_updates: list[str] = Field(default_factory=list, max_length=10)
    pending_actions: list[PendingActionProposal] = Field(default_factory=list, max_length=8)


class AgentUsage(BaseModel):
    """Actual provider usage reported by PydanticAI for one agent run."""

    agent: AgentName
    requests: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    details: dict[str, int] = Field(default_factory=dict)


class TeamRunReport(BaseModel):
    """Result and actual usage from one completed team invocation."""

    run_id: str
    result: AgentReply
    usage: list[AgentUsage]
    pending_actions: list[PendingActionProposal] = Field(default_factory=list, max_length=40)
    pending_action_candidates: list[dict[str, object]] = Field(default_factory=list, max_length=40)


class TeamRequest(BaseModel):
    """A user request accepted by the team entry point."""

    task: str = Field(min_length=1, max_length=4_000)
    ticket_id: int | None = None


class TeamRunLimits(BaseModel):
    """Hard ceilings applied to each invocation and delegation chain."""

    max_requests: int = Field(default=12, ge=1, le=30)
    max_agent_invocations: int = Field(default=8, ge=1, le=20)
    max_delegations: int = Field(default=8, ge=0, le=20)
    max_delegation_depth: int = Field(default=4, ge=1, le=8)
    max_mcp_tool_calls: int = Field(default=12, ge=0, le=30)
    max_input_chars: int = Field(default=4_000, ge=500, le=10_000)
    max_input_tokens_per_invocation: int = Field(default=10_000, ge=500, le=30_000)
    max_output_tokens_per_request: int = Field(default=1_000, ge=128, le=4_000)
    max_output_tokens_per_run: int = Field(default=1_000, ge=128, le=4_000)


class ApprovedToolAction(BaseModel):
    """One exact MCP write approved by a trusted human-facing caller."""

    tool_name: Literal[
        "record_invoice_payment",
        "record_rent_payment",
        "record_purchase_order",
        "set_inventory_quantity",
        "record_inventory_receipt",
        "advance_simulated_shop_date",
        "update_ticket_status",
    ]
    arguments: dict[str, object]
    approved_by: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_external_exact_approval(self) -> "ApprovedToolAction":
        approver = self.approved_by.strip()
        agent_names = {"boss", "inventory", "accounting", "facilities", "customer_service"}
        normalized_approver = approver.casefold().replace(" ", "_").replace("-", "_")
        if not approver or normalized_approver in agent_names:
            raise ValueError("An agent cannot approve its own MCP write request.")
        if self.arguments.get("approved_by") != approver:
            raise ValueError("approved_by must match the trusted caller's approver.")
        reference = self.arguments.get("approval_reference")
        if not isinstance(reference, str) or not reference.strip():
            raise ValueError("Exact caller approval must include its reference in the tool arguments.")
        return self


def consume_approved_action(
    actions: list[ApprovedToolAction], tool_name: str, arguments: dict[str, object]
) -> ApprovedToolAction:
    """Consume one caller approval only when every MCP argument matches exactly."""
    if tool_name not in MCP_WRITE_TOOLS:
        raise PermissionError(f"{tool_name} is not an approved write action.")
    for index, action in enumerate(actions):
        if action.tool_name == tool_name and action.arguments == arguments:
            return actions.pop(index)
    raise PermissionError(
        f"MCP write {tool_name} is blocked without caller approval for these exact arguments."
    )
