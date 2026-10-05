"""Offline request payload checks for all five configured agent roles."""
from __future__ import annotations

import asyncio
import json
import unittest

import httpx2 as httpx
from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.providers.openai import OpenAIProvider

from backend.models import AgentReply, TeamRunLimits
from backend.openai_responses import AuditedOpenAIResponsesModel
from backend.team import OPENAI_REASONING_EFFORT, _ROLES, model_settings_for_agent, run_team


class ProviderSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_mocked_responses_payload_for_every_role(self) -> None:
        seen: list[dict[str, object]] = []

        async def capture(request: httpx.Request) -> httpx.Response:
            seen.append({"path": request.url.path, "body": json.loads(request.content)})
            return httpx.Response(
                400,
                json={"error": {"message": "offline capture only", "type": "invalid_request_error"}},
                request=request,
            )

        client = AsyncOpenAI(
            api_key="offline-test-key",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(capture)),
            max_retries=0,
        )
        model = AuditedOpenAIResponsesModel(
            "gpt-6-luna", provider=OpenAIProvider(openai_client=client)
        )
        limits = TeamRunLimits()
        self.assertEqual(limits.max_requests, 12)
        self.assertEqual(limits.max_agent_invocations, 8)
        self.assertEqual(limits.max_input_tokens_per_invocation, 10_000)
        self.assertTrue(model.profile.get("openai_supports_reasoning_effort_none"))
        self.assertEqual(
            __import__("inspect").signature(run_team).parameters["openai_reasoning_effort"].default,
            "none",
        )
        settings = model_settings_for_agent(limits, OPENAI_REASONING_EFFORT)
        self.assertEqual(settings["openai_reasoning_effort"], "none")
        self.assertEqual(settings["max_tokens"], 1_000)
        try:
            for role in _ROLES:
                agent = Agent(model, name=role, output_type=AgentReply, retries=0)
                try:
                    await agent.run(
                        "Offline request payload check.", model_settings=settings
                    )
                except Exception:
                    pass
        finally:
            await client.close()

        self.assertEqual(len(seen), len(_ROLES))
        for request, role in zip(seen, _ROLES, strict=True):
            body = request["body"]
            self.assertEqual(request["path"], "/v1/responses", role)
            self.assertEqual(body.get("model"), "gpt-6-luna", role)
            self.assertEqual(body.get("reasoning", {}).get("effort"), "none", role)
            self.assertEqual(body.get("max_output_tokens"), 1_000, role)


if __name__ == "__main__":
    unittest.main()
