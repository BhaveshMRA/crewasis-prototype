"""SQLite storage for cards and their history. One file, no server, no ORM."""
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "crewasis.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY,
    fact_id TEXT NOT NULL,
    play_id TEXT,
    kind TEXT NOT NULL,                 -- work | approval
    parent_card_id INTEGER REFERENCES cards(id),
    category TEXT NOT NULL,
    owner_role TEXT NOT NULL,
    state TEXT NOT NULL,                -- work: Surfaced · Drafted · Executed
                                        -- approval: Pending approval · Approved · Rejected · Withdrawn
    base REAL NOT NULL,
    relevance_score REAL NOT NULL,
    evidence TEXT NOT NULL,
    suggested_action TEXT NOT NULL,
    requires_approval INTEGER NOT NULL,
    gate_rule TEXT,
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS card_events (
    id INTEGER PRIMARY KEY,
    card_id INTEGER NOT NULL REFERENCES cards(id),
    event TEXT NOT NULL,     -- created · drafted · handoff · approval_requested · approved · rejected · withdrawn · executed
    from_role TEXT, to_role TEXT, from_state TEXT, to_state TEXT,
    at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def is_empty(con) -> bool:
    return con.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0


def reset(con):
    con.executescript("DELETE FROM card_events; DELETE FROM cards;")
    con.commit()


def log(con, card_id, event, from_role=None, to_role=None, from_state=None, to_state=None):
    con.execute("INSERT INTO card_events (card_id, event, from_role, to_role, from_state, to_state, at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", (card_id, event, from_role, to_role, from_state, to_state, now()))


def add_card(con, **c) -> int:
    t = now()
    cur = con.execute(
        "INSERT INTO cards (fact_id, play_id, kind, parent_card_id, category, owner_role, state, base, "
        "relevance_score, evidence, suggested_action, requires_approval, gate_rule, note, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (c["fact_id"], c.get("play_id"), c["kind"], c.get("parent_card_id"), c["category"], c["owner_role"],
         c["state"], c["base"], c["relevance_score"], c["evidence"], c["suggested_action"],
         int(bool(c.get("requires_approval"))), c.get("gate_rule"), c.get("note", ""), t, t))
    log(con, cur.lastrowid, "created", to_role=c["owner_role"], to_state=c["state"])
    return cur.lastrowid


def update_card(con, card_id, **fields):
    fields["updated_at"] = now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    con.execute(f"UPDATE cards SET {sets} WHERE id = ?", (*fields.values(), card_id))


def cards(con, kind=None):
    q, args = "SELECT * FROM cards", ()
    if kind:
        q, args = q + " WHERE kind = ?", (kind,)
    return [dict(r) for r in con.execute(q, args)]


def card(con, card_id):
    r = con.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    return dict(r) if r else None


def open_approval(con, work_card_id):
    r = con.execute("SELECT * FROM cards WHERE kind = 'approval' AND parent_card_id = ? "
                    "AND state = 'Pending approval'", (work_card_id,)).fetchone()
    return dict(r) if r else None


def latest_approval(con, work_card_id):
    r = con.execute("SELECT * FROM cards WHERE kind = 'approval' AND parent_card_id = ? "
                    "ORDER BY id DESC LIMIT 1", (work_card_id,)).fetchone()
    return dict(r) if r else None


def events(con, card_id=None):
    q, args = "SELECT * FROM card_events", ()
    if card_id is not None:
        q, args = q + " WHERE card_id = ?", (card_id,)
    return [dict(r) for r in con.execute(q + " ORDER BY id", args)]
