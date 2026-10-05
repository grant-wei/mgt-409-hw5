"""Offline financial MCP checks using only disposable database copies."""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend import models
from mcp_server import server


class FinancialToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="hw5-financial-")
        self.database = Path(self.temp_dir.name) / "campus_customs_test.db"
        # Always derive isolated financial fixtures from the untouched source.
        # The working database now contains approved invoice and rent payments.
        source_database = server.PROJECT_ROOT / "data" / "campus_customs.db"
        shutil.copy2(source_database, self.database)
        self.original_database = server.DATABASE_PATH
        server.DATABASE_PATH = self.database
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO vendors (id, name, specialty, lead_days) VALUES (99, 'Offline test fixture', 'test only', 1)"
            )
            connection.commit()

    def tearDown(self) -> None:
        server.DATABASE_PATH = self.original_database
        self.temp_dir.cleanup()

    def sql(self, query: str, parameters: tuple[object, ...] = ()) -> list[tuple]:
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute(query, parameters).fetchall()

    def set_cash(self, balance: float) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE cash_accounts SET balance = ? WHERE name = 'checking'",
                (balance,),
            )
            connection.commit()

    def rent(self, *, account: str = "checking", reference: str = "human-rent-1"):
        return server.record_rent_payment(
            lease_id=1,
            account=account,
            amount=2400.00,
            approved_by="human-operator",
            approval_reference=reference,
        )

    def purchase(self, *, reference: str = "human-purchase-1"):
        return server.record_purchase_order(
            ticket_id=103,
            vendor_id=99,
            sku="CC-HOOD-NAVY",
            size="M",
            quantity=12,
            quoted_unit_cost=25.00,
            total_amount=300.00,
            supplier_quote_reference="OFFLINE-TEST-QUOTE",
            account="checking",
            approved_by="human-operator",
            approval_reference=reference,
        )

    def test_exact_one_use_approval_and_no_agent_self_approval(self) -> None:
        args = {
            "lease_id": 1,
            "account": "checking",
            "amount": 2400.00,
            "approved_by": "human-operator",
            "approval_reference": "human-rent-1",
        }
        action = models.ApprovedToolAction(
            tool_name="record_rent_payment",
            arguments=args,
            approved_by="human-operator",
        )
        actions = [action]
        with self.assertRaises(PermissionError):
            models.consume_approved_action(actions, "record_rent_payment", {**args, "amount": 1.00})
        self.assertEqual(len(actions), 1)
        models.consume_approved_action(actions, "record_rent_payment", args)
        self.assertEqual(actions, [])
        with self.assertRaises(PermissionError):
            models.consume_approved_action(actions, "record_rent_payment", args)
        with self.assertRaises(ValueError):
            models.ApprovedToolAction(
                tool_name="record_rent_payment",
                arguments={**args, "approved_by": "accounting"},
                approved_by="Accounting",
            )
        with self.assertRaises(ValueError):
            models.ApprovedToolAction(
                tool_name="record_rent_payment",
                arguments=args,
                approved_by="different-human",
            )

    def test_rent_is_atomic_correct_and_idempotent_per_due_date(self) -> None:
        result = self.rent()
        self.assertTrue(result["updated"], result)
        self.assertEqual(result["due_date"], "2026-09-02")
        self.assertEqual(result["new_balance"], 1000.00)
        self.assertEqual(self.rent()["reason"], "rent_due_date_already_paid")
        self.assertEqual(self.sql("SELECT balance FROM cash_accounts WHERE name='checking'"), [(1000.0,)])
        self.assertEqual(self.sql("SELECT due_date, amount FROM rent_payments"), [("2026-09-02", 2400.0)])
        self.assertEqual(self.sql("SELECT kind, ref_id, amount FROM payments"), [("rent", 1, 2400.0)])
        self.assertEqual(self.sql("SELECT next_due FROM leases WHERE id=1"), [("2026-09-02",)])
        self.assertEqual(server.get_rent_payment_details(1)["payments"][0]["due_date"], "2026-09-02")

    def test_rent_insufficient_funds_leaves_every_record_unchanged(self) -> None:
        self.set_cash(100.00)
        result = self.rent()
        self.assertEqual(result["reason"], "insufficient_cash")
        self.assertEqual(self.sql("SELECT balance FROM cash_accounts WHERE name='checking'"), [(100.0,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM payments"), [(0,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='rent_payments'"), [(0,)])

    def test_purchase_debits_cash_but_never_delivers_or_resolves(self) -> None:
        result = self.purchase()
        self.assertTrue(result["updated"], result)
        self.assertEqual(result["status"], "ordered")
        self.assertFalse(result["inventory_changed"])
        self.assertFalse(result["ticket_status_changed"])
        self.assertEqual(result["new_balance"], 3100.00)
        self.assertEqual(self.purchase()["reason"], "purchase_already_recorded")
        self.assertEqual(self.sql("SELECT balance FROM cash_accounts WHERE name='checking'"), [(3100.0,)])
        self.assertEqual(self.sql("SELECT status, quantity, quoted_unit_cost, total_amount FROM purchases"), [("ordered", 12, 25.0, 300.0)])
        self.assertEqual(self.sql("SELECT kind, ref_id, amount FROM payments"), [("purchase", result["purchase_id"], 300.0)])
        self.assertEqual(self.sql("SELECT qty FROM inventory WHERE sku='CC-HOOD-NAVY' AND size='M'"), [(8,)])
        self.assertEqual(self.sql("SELECT status FROM tickets WHERE id=103"), [("open",)])
        self.assertEqual(server.get_purchase_details(103)["purchases"][0]["status"], "ordered")

    def test_purchase_insufficient_funds_and_wrong_total_do_not_write(self) -> None:
        self.set_cash(200.00)
        result = self.purchase()
        self.assertEqual(result["reason"], "insufficient_cash")
        self.assertEqual(self.sql("SELECT balance FROM cash_accounts WHERE name='checking'"), [(200.0,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM payments"), [(0,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='purchases'"), [(0,)])
        with self.assertRaises(ValueError):
            server.record_purchase_order(
                ticket_id=103, vendor_id=1, sku="CC-HOOD-NAVY", size="M",
                quantity=12, quoted_unit_cost=22.00, total_amount=263.99,
                supplier_quote_reference="OFFLINE-TEST-QUOTE-BAD-TOTAL",
                account="checking", approved_by="human-operator",
                approval_reference="human-purchase-bad-total",
            )
        self.assertEqual(self.sql("SELECT COUNT(*) FROM payments"), [(0,)])

    def test_purchase_blocks_vendor_with_open_unpaid_invoice(self) -> None:
        result = server.record_purchase_order(
            ticket_id=101, vendor_id=1, sku="CC-TEE-WHITE", size="S",
            quantity=1, quoted_unit_cost=8.00, total_amount=8.00,
            supplier_quote_reference="OFFLINE-TEST-QUOTE-BLOCKED",
            account="checking", approved_by="human-operator",
            approval_reference="human-purchase-blocked",
        )
        self.assertEqual(result["reason"], "vendor_has_open_unpaid_invoice")
        self.assertEqual(result["blocking_invoice_id"], 501)
        self.assertEqual(server.get_vendor_details(1)["vendor"]["open_unpaid_invoice_ids"], "501")
        self.assertEqual(self.sql("SELECT balance FROM cash_accounts WHERE name='checking'"), [(3400.0,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM payments"), [(0,)])

    def test_simulated_receipt_is_durable_atomic_approved_and_idempotent(self) -> None:
        source_label = server.SIMULATION_RECEIPT_SOURCE
        approval_args = {
            "sku": "CC-HOOD-NAVY", "size": "M", "received_quantity": 12,
            "receipt_date": "2026-09-07", "source_label": source_label,
            "receipt_reference": "SIM-HOOD-M-2026-09-07-01",
            "approved_by": "human-operator",
            "approval_reference": "human-simulation-receipt-01",
        }
        action = models.ApprovedToolAction(
            tool_name="record_inventory_receipt", arguments=approval_args,
            approved_by="human-operator",
        )
        clock_args = {
            "new_date": "2026-09-07", "approved_by": "human-operator",
            "approval_reference": "human-simulation-clock-01",
        }
        clock_action = models.ApprovedToolAction(
            tool_name="advance_simulated_shop_date", arguments=clock_args,
            approved_by="human-operator",
        )
        actions = [action, clock_action]
        with self.assertRaises(PermissionError):
            models.consume_approved_action(
                actions, "record_inventory_receipt",
                {**approval_args, "received_quantity": 13},
            )
        self.assertEqual(len(actions), 2)
        models.consume_approved_action(actions, "record_inventory_receipt", approval_args)
        self.assertEqual(actions, [clock_action])
        models.consume_approved_action(actions, "advance_simulated_shop_date", clock_args)
        self.assertEqual(actions, [])
        with self.assertRaises(PermissionError):
            models.consume_approved_action([], "advance_simulated_shop_date", clock_args)
        with self.assertRaises(ValueError):
            models.ApprovedToolAction(
                tool_name="record_inventory_receipt", arguments=approval_args,
                approved_by="inventory",
            )

        before_cash = self.sql("SELECT balance FROM cash_accounts WHERE name='checking'")
        before_statuses = self.sql("SELECT id,status,sku,size FROM tickets WHERE id IN (101,103) ORDER BY id")
        self.assertEqual(self.sql("SELECT qty FROM inventory WHERE sku='CC-HOOD-NAVY' AND size='M'"), [(8,)])
        future = server.record_inventory_receipt(**approval_args)
        self.assertEqual(future["reason"], "receipt_date_after_shop_date")
        self.assertEqual(future["shop_date"], "2026-08-31")
        self.assertEqual(self.sql("SELECT qty FROM inventory WHERE sku='CC-HOOD-NAVY' AND size='M'"), [(8,)])
        self.assertEqual(self.sql("SELECT name FROM sqlite_master WHERE type='table' AND name='inventory_receipts'"), [])

        # Only this temporary fixture's simulated date advances to the receipt date.
        clock = server.advance_simulated_shop_date(**clock_args)
        self.assertTrue(clock["updated"], clock)
        self.assertEqual((clock["previous_date"], clock["new_date"]), ("2026-08-31", "2026-09-07"))
        recorded = server.record_inventory_receipt(**approval_args)
        self.assertTrue(recorded["updated"], recorded)
        self.assertEqual((recorded["previous_stock"], recorded["resulting_stock"]), (8, 20))
        receipt = server.get_inventory_receipt_details("SIM-HOOD-M-2026-09-07-01")
        self.assertTrue(receipt["found"], receipt)
        self.assertEqual(receipt["receipt"]["receipt_date"], "2026-09-07")
        self.assertEqual(receipt["receipt"]["source_label"], source_label)
        self.assertEqual(receipt["receipt"]["previous_stock"], 8)
        self.assertEqual(receipt["receipt"]["resulting_stock"], 20)
        self.assertEqual(receipt["receipt"]["approved_by"], "human-operator")
        self.assertEqual(receipt["receipt"]["approval_reference"], "human-simulation-receipt-01")

        replay = server.record_inventory_receipt(**approval_args)
        self.assertEqual(replay["reason"], "receipt_or_approval_reference_already_used")
        second_reference = server.record_inventory_receipt(
            **{**approval_args, "receipt_reference": "SIM-HOOD-M-2026-09-07-02",
               "approval_reference": "human-simulation-receipt-02"}
        )
        self.assertEqual(second_reference["reason"], "duplicate_receipt")
        self.assertEqual(self.sql("SELECT qty FROM inventory WHERE sku='CC-HOOD-NAVY' AND size='M'"), [(20,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM inventory_receipts"), [(1,)])
        self.assertEqual(self.sql("SELECT balance FROM cash_accounts WHERE name='checking'"), before_cash)
        self.assertEqual(self.sql("SELECT id,status,sku,size FROM tickets WHERE id IN (101,103) ORDER BY id"), before_statuses)
        self.assertEqual(self.sql("SELECT qty FROM inventory WHERE sku='CC-TEE-WHITE' AND size='S'"), [(0,)])


if __name__ == "__main__":
    unittest.main()
