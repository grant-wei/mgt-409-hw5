"""MCP tools for approved, auditable Campus Customs database workflows."""

from pathlib import Path
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation
import math
import os
import sqlite3
from typing import Any

from fastmcp import FastMCP


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = Path(os.environ.get("CAMPUS_CUSTOMS_DB", str(PROJECT_ROOT / "data" / "campus_customs_new.db")))

mcp = FastMCP("Campus Customs")

SIMULATION_RECEIPT_SOURCE = (
    "human-provided simulation input, not verified supplier evidence"
)


def _as_cents(value: float, label: str) -> int:
    """Convert a finite currency value to exact cents or reject it."""
    if not math.isfinite(value):
        raise ValueError(f"{label} must be a finite currency amount.")
    try:
        decimal_value = Decimal(str(value))
        cents = decimal_value * 100
        if cents != cents.to_integral_value():
            raise ValueError(f"{label} must have no more than two decimal places.")
        return int(cents)
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be a valid currency amount.") from exc


def _ensure_financial_tables(connection: sqlite3.Connection) -> None:
    """Create explicit rent-period and undelivered-purchase records in place."""
    connection.execute(
        """CREATE TABLE IF NOT EXISTS rent_payments (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               lease_id INTEGER NOT NULL REFERENCES leases(id),
               due_date TEXT NOT NULL,
               amount REAL NOT NULL CHECK (amount > 0),
               account TEXT NOT NULL REFERENCES cash_accounts(name),
               paid_at TEXT NOT NULL,
               approved_by TEXT NOT NULL,
               approval_reference TEXT NOT NULL UNIQUE,
               UNIQUE (lease_id, due_date)
           )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS purchases (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               ticket_id INTEGER NOT NULL REFERENCES tickets(id),
               vendor_id INTEGER NOT NULL REFERENCES vendors(id),
               sku TEXT NOT NULL,
               size TEXT NOT NULL,
               quantity INTEGER NOT NULL CHECK (quantity > 0),
               quoted_unit_cost REAL NOT NULL CHECK (quoted_unit_cost >= 0),
               total_amount REAL NOT NULL CHECK (total_amount > 0),
               supplier_quote_reference TEXT NOT NULL,
               account TEXT NOT NULL REFERENCES cash_accounts(name),
               status TEXT NOT NULL DEFAULT 'ordered' CHECK (status = 'ordered'),
               ordered_at TEXT NOT NULL,
               approved_by TEXT NOT NULL,
               approval_reference TEXT NOT NULL UNIQUE
           )"""
    )


def _fetch_one(query: str, parameters: tuple[Any, ...]) -> dict[str, Any] | None:
    """Run one parameterized SELECT against the working database, read-only."""
    database_uri = f"{DATABASE_PATH.as_uri()}?mode=ro"
    with closing(sqlite3.connect(database_uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(query, parameters).fetchone()
        return dict(row) if row is not None else None


def _fetch_all(query: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Run one parameterized SELECT against the working database, read-only."""
    database_uri = f"{DATABASE_PATH.as_uri()}?mode=ro"
    with closing(sqlite3.connect(database_uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(query, parameters).fetchall()]


@mcp.tool(annotations={"readOnlyHint": True})
def get_product_snapshot(sku: str, size: str) -> dict[str, Any]:
    """Return the matching inventory row and SKU pricing, if present."""
    row = _fetch_one(
        """
        SELECT inventory.sku, inventory.name, inventory.size, inventory.qty,
               inventory.location, pricing.unit_cost, pricing.list_price
        FROM inventory
        LEFT JOIN pricing ON pricing.sku = inventory.sku
        WHERE inventory.sku = ? AND inventory.size = ?
        """,
        (sku, size),
    )
    return {"found": row is not None, "product": row}


@mcp.tool(annotations={"readOnlyHint": True})
def get_invoice_details(invoice_id: int) -> dict[str, Any]:
    """Return one invoice and its referenced vendor record, if present."""
    row = _fetch_one(
        """
        SELECT invoices.id, invoices.vendor_id, vendors.name AS vendor_name,
               vendors.specialty AS vendor_specialty,
               vendors.lead_days AS vendor_lead_days,
               invoices.amount, invoices.due_date, invoices.status,
               invoices.description
        FROM invoices
        LEFT JOIN vendors ON vendors.id = invoices.vendor_id
        WHERE invoices.id = ?
        """,
        (invoice_id,),
    )
    return {"found": row is not None, "invoice": row}


@mcp.tool(annotations={"readOnlyHint": True})
def get_invoice_payment_details(invoice_id: int) -> dict[str, Any]:
    """Return payment ledger rows linked to one invoice, without changing data."""
    rows = _fetch_all(
        """SELECT id, kind, ref_id, amount, account, paid_at, approved_by
           FROM payments
           WHERE kind = 'invoice' AND ref_id = ?
           ORDER BY id""",
        (invoice_id,),
    )
    return {"invoice_id": invoice_id, "payments": rows}


@mcp.tool(annotations={"readOnlyHint": True})
def get_rent_context(lease_id: int) -> dict[str, Any]:
    """Return one lease and the shop operating date from desk, if present."""
    row = _fetch_one(
        """
        SELECT leases.id, leases.space_name, leases.landlord,
               leases.monthly_rent, leases.next_due, leases.notes,
               desk.date_today AS shop_date
        FROM leases
        CROSS JOIN desk
        WHERE leases.id = ?
        """,
        (lease_id,),
    )
    return {"found": row is not None, "lease": row}


@mcp.tool(annotations={"readOnlyHint": True})
def get_ticket_context(ticket_id: int) -> dict[str, Any]:
    """Return one ticket and its linked invoice/vendor or lease facts."""
    row = _fetch_one(
        """
        SELECT tickets.id, tickets.type, tickets.requester, tickets.subject,
               tickets.sku, tickets.size, tickets.qty, tickets.lease_id,
               tickets.invoice_id, tickets.status, tickets.notes,
               tickets.created_at, invoices.status AS invoice_status,
               invoices.amount AS invoice_amount, invoices.due_date AS invoice_due,
               vendors.name AS invoice_vendor, leases.space_name,
               leases.landlord, leases.monthly_rent, leases.next_due AS rent_due
        FROM tickets
        LEFT JOIN invoices ON invoices.id = tickets.invoice_id
        LEFT JOIN vendors ON vendors.id = invoices.vendor_id
        LEFT JOIN leases ON leases.id = tickets.lease_id
        WHERE tickets.id = ?
        """,
        (ticket_id,),
    )
    return {"found": row is not None, "ticket": row}


@mcp.tool(annotations={"readOnlyHint": True})
def get_cash_snapshot() -> dict[str, Any]:
    """Return every cash account and recorded payment total."""
    accounts = _fetch_all(
        "SELECT name, balance, date FROM cash_accounts ORDER BY name"
    )
    payments = _fetch_all(
        """
        SELECT account, COALESCE(SUM(amount), 0) AS total_paid, COUNT(*) AS count
        FROM payments GROUP BY account ORDER BY account
        """
    )
    return {"accounts": accounts, "payments_by_account": payments}


@mcp.tool(annotations={"readOnlyHint": True})
def get_vendor_details(vendor_id: int) -> dict[str, Any]:
    """Return one vendor's name, specialty, and recorded lead time."""
    row = _fetch_one(
        """SELECT vendors.id, vendors.name, vendors.specialty, vendors.lead_days,
                  (SELECT GROUP_CONCAT(invoices.id, ', ')
                   FROM invoices
                   WHERE invoices.vendor_id = vendors.id AND lower(invoices.status) = 'open')
                      AS open_unpaid_invoice_ids
           FROM vendors WHERE vendors.id = ?""",
        (vendor_id,),
    )
    return {"found": row is not None, "vendor": row}


@mcp.tool(annotations={"readOnlyHint": True})
def get_rent_payment_details(lease_id: int) -> dict[str, Any]:
    """Read recorded payments by rent due date for one lease."""
    database_uri = f"{DATABASE_PATH.as_uri()}?mode=ro"
    with closing(sqlite3.connect(database_uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'rent_payments'"
        ).fetchone()
        if exists is None:
            return {"lease_id": lease_id, "payments": []}
        rows = connection.execute(
            """SELECT id, lease_id, due_date, amount, account, paid_at,
                      approved_by, approval_reference
               FROM rent_payments WHERE lease_id = ? ORDER BY due_date""",
            (lease_id,),
        ).fetchall()
    return {"lease_id": lease_id, "payments": [dict(row) for row in rows]}


@mcp.tool(annotations={"readOnlyHint": True})
def get_purchase_details(ticket_id: int) -> dict[str, Any]:
    """Read explicit purchase orders for a ticket without implying delivery."""
    database_uri = f"{DATABASE_PATH.as_uri()}?mode=ro"
    with closing(sqlite3.connect(database_uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'purchases'"
        ).fetchone()
        if exists is None:
            return {"ticket_id": ticket_id, "purchases": []}
        rows = connection.execute(
            """SELECT purchases.id, purchases.ticket_id, purchases.vendor_id,
                      vendors.name AS selected_vendor, purchases.sku, purchases.size,
                      purchases.quantity, purchases.quoted_unit_cost, purchases.total_amount,
                      purchases.supplier_quote_reference,
                      purchases.account, purchases.status, purchases.ordered_at,
                      purchases.approved_by, purchases.approval_reference
               FROM purchases
               JOIN vendors ON vendors.id = purchases.vendor_id
               WHERE purchases.ticket_id = ? ORDER BY purchases.id""",
            (ticket_id,),
        ).fetchall()
    return {"ticket_id": ticket_id, "purchases": [dict(row) for row in rows]}


@mcp.tool(annotations={"readOnlyHint": True})
def get_inventory_receipt_details(receipt_reference: str) -> dict[str, Any]:
    """Read one durable inventory receipt by its unique reference."""
    database_uri = f"{DATABASE_PATH.as_uri()}?mode=ro"
    with closing(sqlite3.connect(database_uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='inventory_receipts'"
        ).fetchone()
        if exists is None:
            return {"found": False, "receipt": None}
        row = connection.execute(
            """SELECT sku, size, received_quantity, receipt_date,
                      previous_stock, resulting_stock, source_label,
                      receipt_reference, approved_by, approval_reference
               FROM inventory_receipts WHERE receipt_reference = ?""",
            (receipt_reference,),
        ).fetchone()
    return {"found": row is not None, "receipt": dict(row) if row else None}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
def advance_simulated_shop_date(
    new_date: str,
    approved_by: str,
    approval_reference: str,
) -> dict[str, Any]:
    """Advance the simulated shop clock after exact human approval. Never move it backwards."""
    if not approved_by.strip() or not approval_reference.strip():
        raise ValueError("Explicit human approval details are required.")
    try:
        requested_date = date.fromisoformat(new_date)
    except (TypeError, ValueError) as exc:
        raise ValueError("new_date must use YYYY-MM-DD format.") from exc
    database_uri = f"{DATABASE_PATH.as_uri()}?mode=rw"
    with closing(sqlite3.connect(database_uri, uri=True, timeout=10, isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute("SELECT rowid, date_today FROM desk").fetchall()
        if len(rows) != 1:
            connection.rollback()
            return {"updated": False, "reason": "shop_date_row_count_not_one"}
        previous_date = date.fromisoformat(str(rows[0]["date_today"]))
        if requested_date <= previous_date:
            connection.rollback()
            return {
                "updated": False,
                "reason": "new_date_must_be_after_current_shop_date",
                "current_date": previous_date.isoformat(),
            }
        connection.execute(
            "UPDATE desk SET date_today = ? WHERE rowid = ?",
            (requested_date.isoformat(), rows[0]["rowid"]),
        )
        connection.commit()
    return {
        "updated": True,
        "previous_date": previous_date.isoformat(),
        "new_date": requested_date.isoformat(),
        "approved_by": approved_by.strip(),
        "approval_reference": approval_reference.strip(),
    }


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
def record_invoice_payment(
    invoice_id: int,
    account: str,
    amount: float,
    approved_by: str,
    approval_reference: str,
) -> dict[str, Any]:
    """Record a human-approved full invoice payment with an atomic cash check.

    Call only after a human approves this exact invoice, amount, and account.
    """
    if amount <= 0 or not approved_by.strip() or not approval_reference.strip():
        raise ValueError("Positive amount and explicit human approval details are required.")
    database_uri = f"{DATABASE_PATH.as_uri()}?mode=rw"
    with closing(sqlite3.connect(database_uri, uri=True, timeout=10, isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        invoice = connection.execute(
            "SELECT id, amount, status FROM invoices WHERE id = ?", (invoice_id,)
        ).fetchone()
        if invoice is None:
            connection.rollback()
            return {"updated": False, "reason": "invoice_not_found"}
        if invoice["status"].lower() != "open":
            connection.rollback()
            return {"updated": False, "reason": "invoice_not_open"}
        if round(float(invoice["amount"]), 2) != round(float(amount), 2):
            connection.rollback()
            return {"updated": False, "reason": "amount_must_match_full_invoice"}
        cash = connection.execute(
            "SELECT balance FROM cash_accounts WHERE name = ?", (account,)
        ).fetchone()
        if cash is None:
            connection.rollback()
            return {"updated": False, "reason": "cash_account_not_found"}
        if float(cash["balance"]) < amount:
            connection.rollback()
            return {"updated": False, "reason": "insufficient_cash"}
        if connection.execute(
            "SELECT 1 FROM payments WHERE kind = 'invoice' AND ref_id = ? LIMIT 1",
            (invoice_id,),
        ).fetchone():
            connection.rollback()
            return {"updated": False, "reason": "payment_already_recorded"}
        desk = connection.execute("SELECT date_today FROM desk LIMIT 1").fetchone()
        if desk is None:
            connection.rollback()
            return {"updated": False, "reason": "operating_date_missing"}
        connection.execute(
            """INSERT INTO payments
               (kind, ref_id, amount, account, paid_at, approved_by)
               VALUES ('invoice', ?, ?, ?, ?, ?)""",
            (invoice_id, amount, account, desk["date_today"], approved_by.strip()),
        )
        connection.execute(
            "UPDATE cash_accounts SET balance = balance - ? WHERE name = ?",
            (amount, account),
        )
        connection.execute(
            "UPDATE invoices SET status = 'paid' WHERE id = ?", (invoice_id,)
        )
        new_balance = connection.execute(
            "SELECT balance FROM cash_accounts WHERE name = ?", (account,)
        ).fetchone()["balance"]
        connection.commit()
    return {
        "updated": True,
        "invoice_id": invoice_id,
        "amount": amount,
        "account": account,
        "new_balance": new_balance,
        "approved_by": approved_by.strip(),
        "approval_reference": approval_reference.strip(),
    }


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
def record_rent_payment(
    lease_id: int,
    account: str,
    amount: float,
    approved_by: str,
    approval_reference: str,
) -> dict[str, Any]:
    """Record an exactly approved rent payment for the lease's current due date.

    The caller-side team gate must supply one-use approval for every argument.
    This records the payment and cash outflow, but does not change lease terms or
    mark any ticket resolved.
    """
    amount_cents = _as_cents(amount, "amount")
    if amount_cents <= 0 or not approved_by.strip() or not approval_reference.strip():
        raise ValueError("Positive amount and explicit human approval details are required.")
    database_uri = f"{DATABASE_PATH.as_uri()}?mode=rw"
    with closing(sqlite3.connect(database_uri, uri=True, timeout=10, isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        _ensure_financial_tables(connection)
        lease = connection.execute(
            "SELECT monthly_rent, next_due FROM leases WHERE id = ?", (lease_id,)
        ).fetchone()
        if lease is None:
            connection.rollback()
            return {"updated": False, "reason": "lease_not_found"}
        if _as_cents(float(lease["monthly_rent"]), "recorded rent") != amount_cents:
            connection.rollback()
            return {"updated": False, "reason": "amount_must_match_recorded_rent"}
        due_date = str(lease["next_due"])
        if connection.execute(
            "SELECT 1 FROM rent_payments WHERE lease_id = ? AND due_date = ?",
            (lease_id, due_date),
        ).fetchone():
            connection.rollback()
            return {"updated": False, "reason": "rent_due_date_already_paid"}
        if connection.execute(
            "SELECT 1 FROM rent_payments WHERE approval_reference = ?",
            (approval_reference.strip(),),
        ).fetchone():
            connection.rollback()
            return {"updated": False, "reason": "approval_reference_already_used"}
        cash = connection.execute(
            "SELECT balance FROM cash_accounts WHERE name = ?", (account,)
        ).fetchone()
        if cash is None:
            connection.rollback()
            return {"updated": False, "reason": "cash_account_not_found"}
        cash_cents = _as_cents(float(cash["balance"]), "cash balance")
        if cash_cents < amount_cents:
            connection.rollback()
            return {"updated": False, "reason": "insufficient_cash"}
        desk = connection.execute("SELECT date_today FROM desk LIMIT 1").fetchone()
        if desk is None:
            connection.rollback()
            return {"updated": False, "reason": "operating_date_missing"}
        paid_at = str(desk["date_today"])
        approver = approved_by.strip()
        approval_ref = approval_reference.strip()
        cursor = connection.execute(
            """INSERT INTO rent_payments
               (lease_id, due_date, amount, account, paid_at, approved_by, approval_reference)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (lease_id, due_date, amount_cents / 100, account, paid_at, approver, approval_ref),
        )
        rent_payment_id = int(cursor.lastrowid)
        connection.execute(
            """INSERT INTO payments (kind, ref_id, amount, account, paid_at, approved_by)
               VALUES ('rent', ?, ?, ?, ?, ?)""",
            (lease_id, amount_cents / 100, account, paid_at, approver),
        )
        balance_after_cents = cash_cents - amount_cents
        connection.execute(
            "UPDATE cash_accounts SET balance = ? WHERE name = ?",
            (balance_after_cents / 100, account),
        )
        new_balance = connection.execute(
            "SELECT balance FROM cash_accounts WHERE name = ?", (account,)
        ).fetchone()["balance"]
        connection.commit()
    return {
        "updated": True,
        "rent_payment_id": rent_payment_id,
        "lease_id": lease_id,
        "due_date": due_date,
        "paid_at": paid_at,
        "amount": amount_cents / 100,
        "account": account,
        "new_balance": new_balance,
        "approved_by": approver,
        "approval_reference": approval_ref,
    }


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
def record_purchase_order(
    ticket_id: int,
    vendor_id: int,
    sku: str,
    size: str,
    quantity: int,
    quoted_unit_cost: float,
    total_amount: float,
    supplier_quote_reference: str,
    account: str,
    approved_by: str,
    approval_reference: str,
) -> dict[str, Any]:
    """Record and pay for a human-approved purchase without changing inventory.

    Vendor, SKU, size, quantity, quoted price, quote reference, total, and account
    must all match the caller's one-use approval. Quote facts are supplied by the
    human and are not inferred from product pricing or vendor records. The selected
    vendor is not asserted to have a database relationship to the SKU. The purchase
    remains ordered, not delivered, and does not resolve the ticket.
    """
    if quantity <= 0:
        raise ValueError("Purchase quantity must be positive.")
    quote_cents = _as_cents(quoted_unit_cost, "quoted_unit_cost")
    total_cents = _as_cents(total_amount, "total_amount")
    if quote_cents < 0 or total_cents <= 0:
        raise ValueError("Quoted unit price cannot be negative and total must be positive.")
    if quote_cents * quantity != total_cents:
        raise ValueError("total_amount must equal quantity multiplied by quoted_unit_cost.")
    if not all((sku.strip(), size.strip(), supplier_quote_reference.strip(), account.strip(), approved_by.strip(), approval_reference.strip())):
        raise ValueError("Product, supplier quote reference, account, and explicit human approval are required.")
    database_uri = f"{DATABASE_PATH.as_uri()}?mode=rw"
    with closing(sqlite3.connect(database_uri, uri=True, timeout=10, isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        _ensure_financial_tables(connection)
        ticket = connection.execute(
            "SELECT sku, size FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        if ticket is None:
            connection.rollback()
            return {"updated": False, "reason": "ticket_not_found"}
        if ticket["sku"] != sku or ticket["size"] != size:
            connection.rollback()
            return {"updated": False, "reason": "product_does_not_match_ticket"}
        product = connection.execute(
            "SELECT 1 FROM pricing WHERE sku = ?", (sku,)
        ).fetchone()
        if product is None:
            connection.rollback()
            return {"updated": False, "reason": "product_pricing_record_not_found"}
        vendor = connection.execute(
            "SELECT id FROM vendors WHERE id = ?", (vendor_id,)
        ).fetchone()
        if vendor is None:
            connection.rollback()
            return {"updated": False, "reason": "vendor_not_found"}
        open_invoice = connection.execute(
            """SELECT id FROM invoices
               WHERE vendor_id = ? AND lower(status) = 'open' LIMIT 1""",
            (vendor_id,),
        ).fetchone()
        if open_invoice is not None:
            connection.rollback()
            return {
                "updated": False,
                "reason": "vendor_has_open_unpaid_invoice",
                "blocking_invoice_id": int(open_invoice["id"]),
            }
        if connection.execute(
            "SELECT 1 FROM purchases WHERE approval_reference = ?",
            (approval_reference.strip(),),
        ).fetchone():
            connection.rollback()
            return {"updated": False, "reason": "purchase_already_recorded"}
        cash = connection.execute(
            "SELECT balance FROM cash_accounts WHERE name = ?", (account,)
        ).fetchone()
        if cash is None:
            connection.rollback()
            return {"updated": False, "reason": "cash_account_not_found"}
        cash_cents = _as_cents(float(cash["balance"]), "cash balance")
        if cash_cents < total_cents:
            connection.rollback()
            return {"updated": False, "reason": "insufficient_cash"}
        desk = connection.execute("SELECT date_today FROM desk LIMIT 1").fetchone()
        if desk is None:
            connection.rollback()
            return {"updated": False, "reason": "operating_date_missing"}
        approver = approved_by.strip()
        approval_ref = approval_reference.strip()
        cursor = connection.execute(
            """INSERT INTO purchases
               (ticket_id, vendor_id, sku, size, quantity, quoted_unit_cost, total_amount,
                supplier_quote_reference, account, status, ordered_at, approved_by,
                approval_reference)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ordered', ?, ?, ?)""",
            (ticket_id, vendor_id, sku, size, quantity, quote_cents / 100,
             total_cents / 100, supplier_quote_reference.strip(), account,
             desk["date_today"], approver, approval_ref),
        )
        purchase_id = int(cursor.lastrowid)
        connection.execute(
            """INSERT INTO payments (kind, ref_id, amount, account, paid_at, approved_by)
               VALUES ('purchase', ?, ?, ?, ?, ?)""",
            (purchase_id, total_cents / 100, account, desk["date_today"], approver),
        )
        balance_after_cents = cash_cents - total_cents
        connection.execute(
            "UPDATE cash_accounts SET balance = ? WHERE name = ?",
            (balance_after_cents / 100, account),
        )
        new_balance = connection.execute(
            "SELECT balance FROM cash_accounts WHERE name = ?", (account,)
        ).fetchone()["balance"]
        connection.commit()
    return {
        "updated": True,
        "purchase_id": purchase_id,
        "ticket_id": ticket_id,
        "vendor_id": vendor_id,
        "sku": sku,
        "size": size,
        "quantity": quantity,
        "quoted_unit_cost": quote_cents / 100,
        "total_amount": total_cents / 100,
        "supplier_quote_reference": supplier_quote_reference.strip(),
        "account": account,
        "status": "ordered",
        "inventory_changed": False,
        "ticket_status_changed": False,
        "new_balance": new_balance,
        "approved_by": approver,
        "approval_reference": approval_ref,
    }


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
def set_inventory_quantity(
    sku: str,
    size: str,
    quantity: int,
    reason: str,
    approved_by: str,
    approval_reference: str,
) -> dict[str, Any]:
    """Set one size's on-hand quantity after human approval of the exact change."""
    if quantity < 0:
        raise ValueError("Inventory quantity cannot be negative.")
    if not reason.strip() or not approved_by.strip() or not approval_reference.strip():
        raise ValueError("Reason and explicit human approval details are required.")
    database_uri = f"{DATABASE_PATH.as_uri()}?mode=rw"
    with closing(sqlite3.connect(database_uri, uri=True, timeout=10, isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        current = connection.execute(
            "SELECT qty FROM inventory WHERE sku = ? AND size = ?", (sku, size)
        ).fetchone()
        if current is None:
            return {"updated": False, "reason": "inventory_row_not_found"}
        old_quantity = int(current["qty"])
        connection.execute(
            "UPDATE inventory SET qty = ? WHERE sku = ? AND size = ?",
            (quantity, sku, size),
        )
        connection.commit()
    return {
        "updated": True,
        "sku": sku,
        "size": size,
        "previous_quantity": old_quantity,
        "quantity": quantity,
        "reason": reason.strip(),
        "approved_by": approved_by.strip(),
        "approval_reference": approval_reference.strip(),
    }


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
def record_inventory_receipt(
    sku: str,
    size: str,
    received_quantity: int,
    receipt_date: str,
    source_label: str,
    receipt_reference: str,
    approved_by: str,
    approval_reference: str,
) -> dict[str, Any]:
    """Atomically record an exactly approved human-input simulation receipt and stock increase.

    This receipt type records simulation input only, not supplier-verified evidence.
    """
    if type(received_quantity) is not int or received_quantity <= 0:
        raise ValueError("received_quantity must be a positive integer.")
    if not sku.strip() or not size.strip():
        raise ValueError("SKU and size are required.")
    if source_label != SIMULATION_RECEIPT_SOURCE:
        raise ValueError(
            "source_label must explicitly identify human-provided simulation input, not verified supplier evidence."
        )
    if not receipt_reference.strip() or not approved_by.strip() or not approval_reference.strip():
        raise ValueError("Unique receipt reference and exact human approval details are required.")
    try:
        parsed_receipt_date = date.fromisoformat(receipt_date)
    except (TypeError, ValueError) as exc:
        raise ValueError("receipt_date must use YYYY-MM-DD format.") from exc

    database_uri = f"{DATABASE_PATH.as_uri()}?mode=rw"
    with closing(sqlite3.connect(database_uri, uri=True, timeout=10, isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS inventory_receipts (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   sku TEXT NOT NULL,
                   size TEXT NOT NULL,
                   received_quantity INTEGER NOT NULL CHECK (received_quantity > 0),
                   receipt_date TEXT NOT NULL,
                   previous_stock INTEGER NOT NULL CHECK (previous_stock >= 0),
                   resulting_stock INTEGER NOT NULL CHECK (resulting_stock >= previous_stock),
                   source_label TEXT NOT NULL CHECK (
                       source_label = 'human-provided simulation input, not verified supplier evidence'
                   ),
                   receipt_reference TEXT NOT NULL UNIQUE,
                   approved_by TEXT NOT NULL,
                   approval_reference TEXT NOT NULL UNIQUE,
                   UNIQUE (sku, size, received_quantity, receipt_date, source_label),
                   FOREIGN KEY (sku, size) REFERENCES inventory(sku, size)
               )"""
        )
        desk = connection.execute("SELECT date_today FROM desk LIMIT 1").fetchone()
        if desk is None:
            connection.rollback()
            return {"updated": False, "reason": "shop_date_missing"}
        shop_date = date.fromisoformat(str(desk["date_today"]))
        if parsed_receipt_date > shop_date:
            connection.rollback()
            return {
                "updated": False,
                "reason": "receipt_date_after_shop_date",
                "receipt_date": parsed_receipt_date.isoformat(),
                "shop_date": shop_date.isoformat(),
            }
        if connection.execute(
            "SELECT 1 FROM inventory_receipts WHERE receipt_reference = ? OR approval_reference = ?",
            (receipt_reference.strip(), approval_reference.strip()),
        ).fetchone():
            connection.rollback()
            return {"updated": False, "reason": "receipt_or_approval_reference_already_used"}
        current = connection.execute(
            "SELECT qty FROM inventory WHERE sku = ? AND size = ?", (sku, size)
        ).fetchone()
        if current is None:
            connection.rollback()
            return {"updated": False, "reason": "inventory_row_not_found"}
        previous_stock = int(current["qty"])
        resulting_stock = previous_stock + received_quantity
        try:
            connection.execute(
                """INSERT INTO inventory_receipts
                   (sku, size, received_quantity, receipt_date, previous_stock,
                    resulting_stock, source_label, receipt_reference, approved_by,
                    approval_reference)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (sku, size, received_quantity, parsed_receipt_date.isoformat(),
                 previous_stock, resulting_stock, source_label, receipt_reference.strip(),
                 approved_by.strip(), approval_reference.strip()),
            )
        except sqlite3.IntegrityError:
            connection.rollback()
            return {"updated": False, "reason": "duplicate_receipt"}
        connection.execute(
            "UPDATE inventory SET qty = ? WHERE sku = ? AND size = ?",
            (resulting_stock, sku, size),
        )
        connection.commit()
    return {
        "updated": True,
        "sku": sku,
        "size": size,
        "received_quantity": received_quantity,
        "receipt_date": parsed_receipt_date.isoformat(),
        "previous_stock": previous_stock,
        "resulting_stock": resulting_stock,
        "source_label": source_label,
        "receipt_reference": receipt_reference.strip(),
        "approved_by": approved_by.strip(),
        "approval_reference": approval_reference.strip(),
    }


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
def update_ticket_status(
    ticket_id: int,
    status: str,
    approved_by: str,
    approval_reference: str,
) -> dict[str, Any]:
    """Change a ticket status only after a human approves the exact status."""
    if status not in {"open", "pending", "resolved"}:
        raise ValueError("Ticket status must be open, pending, or resolved.")
    if not approved_by.strip() or not approval_reference.strip():
        raise ValueError("Explicit human approval details are required.")
    database_uri = f"{DATABASE_PATH.as_uri()}?mode=rw"
    with closing(sqlite3.connect(database_uri, uri=True, timeout=10, isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT status FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        if row is None:
            return {"updated": False, "reason": "ticket_not_found"}
        previous_status = row["status"]
        connection.execute(
            "UPDATE tickets SET status = ? WHERE id = ?", (status, ticket_id)
        )
        connection.commit()
    return {
        "updated": True,
        "ticket_id": ticket_id,
        "previous_status": previous_status,
        "status": status,
        "approved_by": approved_by.strip(),
        "approval_reference": approval_reference.strip(),
    }


if __name__ == "__main__":
    mcp.run()
