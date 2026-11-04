"""
Seeds support_agent.db with dummy data so you can test the agent
without entering everything through the keyboard menu first.

Run once:
    python seed_database.py

Safe to re-run — it overwrites matching rows rather than duplicating them.
"""

import os
import sqlite3
import datetime as dt

# Must match support_agent_v3.py's DB_PATH exactly, or seeding writes to a
# different file than the agent reads from.
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "support_agent.db")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS customers (
            customer_id TEXT PRIMARY KEY,
            name TEXT,
            state TEXT DEFAULT 'open',
            last_inbound_at TEXT,
            last_outbound_at TEXT,
            followup_count INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            customer_id TEXT,
            item_name TEXT,
            status TEXT,
            eta TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            product_name TEXT PRIMARY KEY,
            quantity INTEGER,
            expiry_date TEXT
        )
        """
    )
    return conn


def seed() -> None:
    conn = get_db()
    now = dt.datetime.now(dt.UTC).isoformat()
    stale = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=30)).isoformat()  # >24h ago, for testing follow-ups

    # --- Customers -----------------------------------------------------
    customers = [
        # customer_id, name,     state,           last_inbound_at, last_outbound_at, followup_count
        ("cust_1", "Priya",      "open",          now,   None,   0),
        ("cust_2", "John",       "open",          now,   None,   0),
        ("cust_3", "Aisha",      "awaiting_reply", stale, stale, 0),  # due for a follow-up reminder
        ("cust_4", "Marcus",     "opted_out",     now,   now,    1),  # already opted out
    ]
    for customer_id, name, state, last_inbound_at, last_outbound_at, followup_count in customers:
        conn.execute(
            """
            INSERT INTO customers (customer_id, name, state, last_inbound_at, last_outbound_at, followup_count)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(customer_id) DO UPDATE SET
                name=excluded.name, state=excluded.state,
                last_inbound_at=excluded.last_inbound_at,
                last_outbound_at=excluded.last_outbound_at,
                followup_count=excluded.followup_count
            """,
            (customer_id, name, state, last_inbound_at, last_outbound_at, followup_count),
        )

    # --- Orders ----------------------------------------------------------
    orders = [
        # order_id, customer_id, item_name,         status,             eta
        ("#6",  "cust_1", "Wireless Earbuds", "Shipped",            "2026-11-07"),
        ("#7",  "cust_1", "Phone Case",        "Out for Delivery",  "2026-11-05"),
        ("#12", "cust_2", "Desk Lamp",         "Processing",        "2026-11-10"),
        ("#20", "cust_3", "Yoga Mat",          "Delivered",         "2026-10-30"),
    ]
    for order_id, customer_id, item_name, status, eta in orders:
        conn.execute(
            """
            INSERT INTO orders (order_id, customer_id, item_name, status, eta, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(order_id) DO UPDATE SET
                customer_id=excluded.customer_id, item_name=excluded.item_name,
                status=excluded.status, eta=excluded.eta
            """,
            (order_id, customer_id, item_name, status, eta, now),
        )

    # --- Products ----------------------------------------------------------
    # Today is treated as 2026-11-04 for these examples.
    products = [
        # product_name,          quantity, expiry_date
        ("Wireless Earbuds",     15,  None),          # in stock, not perishable
        ("Phone Case",           0,   None),           # OUT OF STOCK
        ("Desk Lamp",            8,   None),           # in stock
        ("Yoga Mat",             20,  None),           # in stock
        ("Protein Bar",          5,   "2026-10-01"),   # EXPIRED
        ("Vitamin C Tablets",    12,  "2027-03-01"),   # in stock, not expired
    ]
    for product_name, quantity, expiry_date in products:
        conn.execute(
            """
            INSERT INTO products (product_name, quantity, expiry_date)
            VALUES (?, ?, ?)
            ON CONFLICT(product_name) DO UPDATE SET
                quantity=excluded.quantity, expiry_date=excluded.expiry_date
            """,
            (product_name, quantity, expiry_date),
        )

    conn.commit()
    conn.close()
    print("Seeded support_agent.db with dummy customers, orders, and products.")


if __name__ == "__main__":
    seed()