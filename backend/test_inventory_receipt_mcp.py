"""Receipt MCP protocol regression on a disposable original-database copy."""
from __future__ import annotations

import asyncio
import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from backend.main import ORIGINAL_DATABASE_PATH, WORKING_DATABASE_PATH
from backend.team import MCP_SERVER_PATH, PROJECT_ROOT
from mcp_server.server import SIMULATION_RECEIPT_SOURCE


class InventoryReceiptMCPTests(unittest.TestCase):
    def test_date_gate_durable_receipt_replay_and_isolation_over_stdio(self) -> None:
        original_hash = hashlib.sha256(ORIGINAL_DATABASE_PATH.read_bytes()).hexdigest()
        working_hash = hashlib.sha256(WORKING_DATABASE_PATH.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="hw5-receipt-mcp-") as folder:
            temp_db = Path(folder) / "campus_customs_receipt_test.db"
            # Final evidence can contain approved simulations. Use the stable
            # original fixture so this test is independent of shop progress.
            shutil.copy2(ORIGINAL_DATABASE_PATH, temp_db)
            asyncio.run(self._exercise(temp_db))
        self.assertEqual(hashlib.sha256(ORIGINAL_DATABASE_PATH.read_bytes()).hexdigest(), original_hash)
        self.assertEqual(hashlib.sha256(WORKING_DATABASE_PATH.read_bytes()).hexdigest(), working_hash)

    async def _exercise(self, temp_db: Path) -> None:
        client = Client(
            StdioTransport(
                command=sys.executable,
                args=[str(MCP_SERVER_PATH)],
                env={"CAMPUS_CUSTOMS_DB": str(temp_db)},
                cwd=str(PROJECT_ROOT),
            ),
            name="receipt-regression",
        )
        async with client:
            before = (await client.call_tool("get_product_snapshot", {"sku": "CC-HOOD-NAVY", "size": "M"})).data
            self.assertEqual(before["product"]["qty"], 8)
            args = {
                "sku": "CC-HOOD-NAVY", "size": "M", "received_quantity": 12,
                "receipt_date": "2026-09-07", "source_label": SIMULATION_RECEIPT_SOURCE,
                "receipt_reference": "SIM-HOOD-M-2026-09-07-01",
                "approved_by": "human-operator",
                "approval_reference": "human-simulation-receipt-01",
            }
            future = (await client.call_tool("record_inventory_receipt", args)).data
            self.assertEqual(future["reason"], "receipt_date_after_shop_date")
            self.assertEqual(future["shop_date"], "2026-08-31")
            after_rejection = (await client.call_tool("get_product_snapshot", {"sku": "CC-HOOD-NAVY", "size": "M"})).data
            self.assertEqual(after_rejection["product"]["qty"], 8)

            # This clock advance is confined to the disposable database and uses MCP.
            clock = (await client.call_tool("advance_simulated_shop_date", {
                "new_date": "2026-09-07", "approved_by": "human-operator",
                "approval_reference": "human-simulation-clock-01",
            })).data
            self.assertTrue(clock["updated"], clock)
            self.assertEqual((clock["previous_date"], clock["new_date"]), ("2026-08-31", "2026-09-07"))
            recorded = (await client.call_tool("record_inventory_receipt", args)).data
            self.assertTrue(recorded["updated"], recorded)
            self.assertEqual(recorded["previous_stock"], 8)
            self.assertEqual(recorded["resulting_stock"], 20)
            stored = (await client.call_tool(
                "get_inventory_receipt_details", {"receipt_reference": args["receipt_reference"]}
            )).data
            self.assertTrue(stored["found"], stored)
            self.assertEqual(stored["receipt"]["receipt_date"], "2026-09-07")
            self.assertEqual(stored["receipt"]["source_label"], SIMULATION_RECEIPT_SOURCE)
            self.assertEqual(stored["receipt"]["received_quantity"], 12)
            self.assertEqual(stored["receipt"]["previous_stock"], 8)
            self.assertEqual(stored["receipt"]["resulting_stock"], 20)
            self.assertEqual(stored["receipt"]["receipt_reference"], args["receipt_reference"])
            self.assertEqual(stored["receipt"]["approved_by"], "human-operator")
            self.assertEqual(stored["receipt"]["approval_reference"], "human-simulation-receipt-01")

            replay = (await client.call_tool("record_inventory_receipt", args)).data
            self.assertFalse(replay["updated"])
            self.assertEqual(replay["reason"], "receipt_or_approval_reference_already_used")
            after_replay = (await client.call_tool("get_product_snapshot", {"sku": "CC-HOOD-NAVY", "size": "M"})).data
            self.assertEqual(after_replay["product"]["qty"], 20)
            cash = (await client.call_tool("get_cash_snapshot", {})).data
            self.assertEqual(next(row["balance"] for row in cash["accounts"] if row["name"] == "checking"), 3400.0)
            tickets = [
                (await client.call_tool("get_ticket_context", {"ticket_id": ticket_id})).data["ticket"]
                for ticket_id in (101, 103)
            ]
            self.assertEqual((tickets[0]["sku"], tickets[0]["size"], tickets[0]["status"]), ("CC-TEE-WHITE", "S", "open"))
            self.assertEqual((tickets[1]["sku"], tickets[1]["size"], tickets[1]["status"]), ("CC-HOOD-NAVY", "M", "open"))


if __name__ == "__main__":
    unittest.main()
