"""
Support agent v3 — adds:
  - Real order/shipment lookups (no more mocked ETA/status) backed by SQLite.
  - Keyboard-driven data entry: add customers, orders, and products interactively.
  - A tool to check product availability AND expiry date together.

Still fully local: Ollama handles both embeddings and chat generation.

Setup:
    pip install ollama chromadb
    ollama pull nomic-embed-text
    ollama pull llama3.1
"""

import os
import json
import re
import sqlite3
import datetime as dt
import ollama
import chromadb

# Anchored to this file's own folder, not the terminal's current directory —
# otherwise running this script and seed_database.py from different folders
# silently creates two different database files.
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "support_agent.db")

# Prints tool calls to the terminal for debugging only — never shown to the
# customer. Set to False once things are working.
DEBUG_TOOL_CALLS = True
FOLLOWUP_AFTER_HOURS = 24
STOP_KEYWORDS = {"stop", "unsubscribe", "opt out", "optout", "no thanks", "cancel"}
RESUME_KEYWORDS = {"start", "subscribe", "resume", "yes please"}

EMBED_MODEL = "nomic-embed-text"
CHAT_MODEL = "llama3.1"

# ---------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------

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
            status TEXT,        -- Processing | Shipped | Out for Delivery | Delivered
            eta TEXT,           -- ISO date, e.g. 2026-11-10
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            product_name TEXT PRIMARY KEY,
            quantity INTEGER,
            expiry_date TEXT    -- ISO date, or NULL if not perishable
        )
        """
    )
    return conn


# ---------------------------------------------------------------------
# Customer state (same as before)
# ---------------------------------------------------------------------

def record_inbound(customer_id: str, name: str) -> None:
    now = dt.datetime.now(dt.UTC).isoformat()
    conn = get_db()
    conn.execute(
        """
        INSERT INTO customers (customer_id, name, state, last_inbound_at, followup_count)
        VALUES (?, ?, 'open', ?, 0)
        ON CONFLICT(customer_id) DO UPDATE SET
            name = excluded.name, state = 'open',
            last_inbound_at = excluded.last_inbound_at, followup_count = 0
        """,
        (customer_id, name, now),
    )
    conn.commit()
    conn.close()


def record_outbound(customer_id: str) -> None:
    now = dt.datetime.now(dt.UTC).isoformat()
    conn = get_db()
    conn.execute(
        """
        UPDATE customers
        SET state = CASE WHEN state = 'opted_out' THEN 'opted_out' ELSE 'awaiting_reply' END,
            last_outbound_at = ?
        WHERE customer_id = ?
        """,
        (now, customer_id),
    )
    conn.commit()
    conn.close()


def mark_opted_out(customer_id: str) -> None:
    conn = get_db()
    conn.execute("UPDATE customers SET state = 'opted_out' WHERE customer_id = ?", (customer_id,))
    conn.commit()
    conn.close()


def mark_opted_in(customer_id: str) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE customers SET state = 'open', followup_count = 0 WHERE customer_id = ?",
        (customer_id,),
    )
    conn.commit()
    conn.close()


def is_opted_out(customer_id: str) -> bool:
    conn = get_db()
    row = conn.execute("SELECT state FROM customers WHERE customer_id = ?", (customer_id,)).fetchone()
    conn.close()
    return bool(row and row[0] == "opted_out")


def get_customers_due_for_followup(hours: int = FOLLOWUP_AFTER_HOURS) -> list[dict]:
    cutoff = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)).isoformat()
    conn = get_db()
    rows = conn.execute(
        """
        SELECT customer_id, name FROM customers
        WHERE state = 'awaiting_reply' AND followup_count = 0 AND last_outbound_at <= ?
        """,
        (cutoff,),
    ).fetchall()
    conn.close()
    return [{"customer_id": r[0], "name": r[1]} for r in rows]


def increment_followup_count(customer_id: str) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE customers SET followup_count = followup_count + 1 WHERE customer_id = ?",
        (customer_id,),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------
# Keyboard-driven data entry
# ---------------------------------------------------------------------

def add_customer_interactive() -> None:
    print("\n-- Add a customer --")
    customer_id = input("Customer ID: ").strip()
    name = input("Customer name: ").strip()
    record_inbound(customer_id, name)
    print(f"Added customer '{name}' ({customer_id}).\n")


def add_order_interactive() -> None:
    print("\n-- Add an order / shipment --")
    order_id = input("Order ID: ").strip()
    customer_id = input("Customer ID: ").strip()
    item_name = input("Item name: ").strip()
    status = input("Status (Processing/Shipped/Out for Delivery/Delivered): ").strip()
    eta = input("ETA (YYYY-MM-DD): ").strip()

    conn = get_db()
    conn.execute(
        """
        INSERT INTO orders (order_id, customer_id, item_name, status, eta, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(order_id) DO UPDATE SET
            customer_id = excluded.customer_id, item_name = excluded.item_name,
            status = excluded.status, eta = excluded.eta
        """,
        (order_id, customer_id, item_name, status, eta, dt.datetime.now(dt.UTC).isoformat()),
    )
    conn.commit()
    conn.close()
    print(f"Order '{order_id}' saved: {item_name} — {status}, ETA {eta}.\n")


def add_product_interactive() -> None:
    print("\n-- Add a product --")
    product_name = input("Product name: ").strip()
    quantity = int(input("Quantity available: ").strip() or "0")
    expiry_date = input("Expiry date (YYYY-MM-DD, blank if not perishable): ").strip() or None

    conn = get_db()
    conn.execute(
        """
        INSERT INTO products (product_name, quantity, expiry_date)
        VALUES (?, ?, ?)
        ON CONFLICT(product_name) DO UPDATE SET
            quantity = excluded.quantity, expiry_date = excluded.expiry_date
        """,
        (product_name, quantity, expiry_date),
    )
    conn.commit()
    conn.close()
    print(f"Product '{product_name}' saved: qty {quantity}, expiry {expiry_date or 'N/A'}.\n")


# ---------------------------------------------------------------------
# Knowledge base (local embeddings)
# ---------------------------------------------------------------------

chroma_client = chromadb.Client()
collection = chroma_client.get_or_create_collection("support_kb")


def embed_text(text: str) -> list[float]:
    return ollama.embeddings(model=EMBED_MODEL, prompt=text)["embedding"]


def index_knowledge_base(docs: list[str]) -> None:
    for i, doc in enumerate(docs):
        collection.add(ids=[str(i)], embeddings=[embed_text(doc)], documents=[doc])


def retrieve_context(query: str, k: int = 3) -> list[str]:
    results = collection.query(query_embeddings=[embed_text(query)], n_results=k)
    return results["documents"][0] if results["documents"] else []


# ---------------------------------------------------------------------
# Tools — now backed by the real database
# ---------------------------------------------------------------------

def get_order_status(order_id: str) -> dict:
    conn = get_db()
    row = conn.execute(
        "SELECT item_name, status, eta FROM orders WHERE order_id = ?", (order_id,)
    ).fetchone()
    conn.close()
    if not row:
        return {"order_id": order_id, "found": False}
    item_name, status, eta = row
    return {"order_id": order_id, "found": True, "item_name": item_name, "status": status, "eta": eta}


def check_product_status(product_name: str) -> dict:
    conn = get_db()
    row = conn.execute(
        "SELECT quantity, expiry_date FROM products WHERE product_name = ?", (product_name,)
    ).fetchone()
    conn.close()
    if not row:
        return {"product_name": product_name, "found": False}

    quantity, expiry_date = row
    expired = False
    if expiry_date:
        expired = dt.date.fromisoformat(expiry_date) < dt.date.today()

    return {
        "product_name": product_name,
        "found": True,
        "in_stock": quantity > 0,
        "quantity": quantity,
        "expiry_date": expiry_date,
        "expired": expired,
    }


def escalate_to_human(reason: str) -> dict:
    return {"escalated": True, "reason": reason}


def opt_out_customer_tool(customer_id: str, reason: str = "") -> dict:
    mark_opted_out(customer_id)
    return {"opted_out": True, "customer_id": customer_id}


available_functions = {
    "get_order_status": get_order_status,
    "check_product_status": check_product_status,
    "escalate_to_human": escalate_to_human,
    "opt_out_customer_tool": opt_out_customer_tool,
}

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_order_status",
            "description": "Look up the real shipping status and ETA of a customer's order by order ID",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_product_status",
            "description": "Check whether a product is in stock and whether it has expired, by product name",
            "parameters": {
                "type": "object",
                "properties": {"product_name": {"type": "string"}},
                "required": ["product_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": "Escalate to a human agent for sensitive or unresolved issues",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "opt_out_customer_tool",
            "description": "Call this if the customer asks, in any wording, to stop receiving messages",
            "parameters": {
                "type": "object",
                "properties": {"customer_id": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["customer_id"],
            },
        },
    },
]

def try_parse_fake_tool_call(content: str) -> dict | None:
    """
    Safety net: some local models occasionally print what a tool call
    'looks like' as plain JSON text instead of actually invoking the
    tool. If that happens, catch it here so it never reaches the
    customer, rather than relying on the model to always behave.

    Uses brace-depth counting (not a flat regex) so it correctly handles
    nested objects like {"name": "...", "parameters": {"order_id": "#6"}}.
    """
    for start in (i for i, ch in enumerate(content) if ch == "{"):
        depth = 0
        for i in range(start, len(content)):
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = content[start:i + 1]
                    try:
                        data = json.loads(candidate)
                    except json.JSONDecodeError:
                        data = None
                    if isinstance(data, dict) and data.get("name") in available_functions:
                        return data
                    break  # this { didn't pan out, try the next one
    return None


# ---------------------------------------------------------------------
# Core agent call (Ollama)
# ---------------------------------------------------------------------

def run_agent(customer_id: str, customer_name: str, customer_message: str,
              chat_history: list[dict] | None = None) -> str:
    chat_history = chat_history or []
    context_chunks = retrieve_context(customer_message)
    context_text = "\n".join(context_chunks) if context_chunks else "No matching KB article found."

    system_prompt = f"""You are a warm, specific customer support agent.

Customer name: {customer_name}
Customer ID: {customer_id}

Relevant knowledge base context:
{context_text}

Rules:
- Address the customer by name.
- Use get_order_status for any question about shipping, tracking, or ETA — never guess a date.
- Use check_product_status before telling a customer an item is available — flag it clearly if expired or out of stock.
- If the customer wants to stop hearing from you in any wording, call opt_out_customer_tool.
- If you can't resolve the issue, call escalate_to_human.
- Keep replies concise and human, not templated.
- NEVER show the customer JSON, code, function names, or tool syntax of any
  kind. If you need information, call the tool silently and only ever reply
  in plain, natural language — the customer should never see how you got
  the answer, only the answer itself."""

    messages = (
        [{"role": "system", "content": system_prompt}]
        + chat_history
        + [{"role": "user", "content": customer_message}]
    )

    response = ollama.chat(model=CHAT_MODEL, messages=messages, tools=tools)
    message = response["message"]

    if message.get("tool_calls"):
        messages.append(message)
        for tool_call in message["tool_calls"]:
            fn_name = tool_call["function"]["name"]
            fn_args = tool_call["function"]["arguments"]
            result = available_functions[fn_name](**fn_args)
            if DEBUG_TOOL_CALLS:
                print(f"[tool call] {fn_name}({fn_args}) -> {result}")
            messages.append({"role": "tool", "content": json.dumps(result)})
        # Explicitly ask for a natural-language answer — without this nudge,
        # some models just echo the tool result back as raw data instead of
        # phrasing it as a sentence.
        messages.append(
            {
                "role": "user",
                "content": "Using that result, reply to the customer in one or two warm, "
                           "natural sentences. Do not show field names, keys, JSON, or "
                           "any raw data — just the answer, phrased the way a person would say it.",
            }
        )
        final = ollama.chat(model=CHAT_MODEL, messages=messages)
        return final["message"]["content"]

    # The model didn't use a real tool call — check if it faked one as text.
    fake_call = try_parse_fake_tool_call(message["content"])
    if fake_call:
        fn_name = fake_call["name"]
        fn_args = fake_call.get("parameters", fake_call.get("arguments", {}))
        result = available_functions[fn_name](**fn_args)
        if DEBUG_TOOL_CALLS:
            print(f"[fake tool call caught] {fn_name}({fn_args}) -> {result}")
        messages.append({"role": "assistant", "content": message["content"]})
        messages.append({"role": "tool", "content": json.dumps(result)})
        messages.append(
            {
                "role": "user",
                "content": "Using that result, answer in plain, friendly language. "
                           "Do not mention JSON, function names, or any code.",
            }
        )
        final = ollama.chat(model=CHAT_MODEL, messages=messages)
        return final["message"]["content"]

    return message["content"]


# ---------------------------------------------------------------------
# Entry point: deterministic STOP/START, then delegate to the LLM
# ---------------------------------------------------------------------

def handle_inbound_message(customer_id: str, name: str, message: str,
                            chat_history: list[dict] | None = None) -> str | None:
    normalized = message.strip().lower()

    if normalized in STOP_KEYWORDS:
        mark_opted_out(customer_id)
        reply = f"Got it, {name} — you're opted out and I won't message you again. Reply START anytime to resume."
        record_outbound(customer_id)
        return reply

    if is_opted_out(customer_id):
        if normalized in RESUME_KEYWORDS:
            mark_opted_in(customer_id)
            record_inbound(customer_id, name)
            reply = f"Welcome back, {name}! Happy to help again — what do you need?"
            record_outbound(customer_id)
            return reply
        return None

    record_inbound(customer_id, name)
    reply = run_agent(customer_id, name, message, chat_history)
    record_outbound(customer_id)
    return reply


# ---------------------------------------------------------------------
# Follow-up job
# ---------------------------------------------------------------------

def send_message_to_customer(customer_id: str, message: str) -> None:
    print(f"[to {customer_id}] {message}")


def send_followups() -> None:
    for customer in get_customers_due_for_followup():
        if is_opted_out(customer["customer_id"]):
            continue
        message = (
            f"Hi {customer['name']}, just checking in on your request — "
            f"still need help with this, or should I close it out? "
            f"Reply STOP anytime if you'd rather I not follow up."
        )
        send_message_to_customer(customer["customer_id"], message)
        record_outbound(customer["customer_id"])
        increment_followup_count(customer["customer_id"])


# ---------------------------------------------------------------------
# CLI menu — populate data and test the chat without editing code
# ---------------------------------------------------------------------

def chat_loop() -> None:
    print("\n-- Chat as a customer (type 'quit' to exit) --")
    customer_id = input("Your customer ID: ").strip()
    name = input("Your name: ").strip()
    history: list[dict] = []
    while True:
        message = input(f"{name}: ").strip()
        if message.lower() == "quit":
            break
        reply = handle_inbound_message(customer_id, name, message, history)
        if reply is None:
            print("(no reply sent — customer is opted out)")
            continue
        print(f"Agent: {reply}")
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": reply})


def main() -> None:
    get_db().close()  # ensure tables exist
    index_knowledge_base(
        [
            "Orders usually ship within 2 business days.",
            "Refunds are processed within 5-7 business days after we receive the return.",
        ]
    )

    menu = """
1. Add a customer
2. Add an order / shipment
3. Add a product
4. Chat as a customer
5. Run the follow-up job
6. Exit
"""
    while True:
        print(menu)
        choice = input("Choose an option: ").strip()
        if choice == "1":
            add_customer_interactive()
        elif choice == "2":
            add_order_interactive()
        elif choice == "3":
            add_product_interactive()
        elif choice == "4":
            chat_loop()
        elif choice == "5":
            send_followups()
        elif choice == "6":
            break
        else:
            print("Not a valid option, try again.")


if __name__ == "__main__":
    main()