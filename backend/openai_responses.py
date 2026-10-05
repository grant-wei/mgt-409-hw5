"""Direct OpenAI Responses model with redacted completion metadata."""

from __future__ import annotations

from typing import Any

from pydantic_ai.models.openai import OpenAIResponsesModel


class AuditedOpenAIResponsesModel(OpenAIResponsesModel):
    """Retain only status metadata needed to diagnose incomplete responses."""

    def _process_provider_details(self, response: Any) -> dict[str, Any]:
        details: dict[str, Any] = {"openai_response_status": response.status}
        incomplete = response.incomplete_details
        if incomplete is not None:
            details["openai_incomplete_details"] = {
                "reason": getattr(incomplete, "reason", None)
            }
        return details
