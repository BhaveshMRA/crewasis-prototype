"""SQLite storage for everything in the architecture: sources, evidence, engagements, facts, play matches,
brief sections, cards, card events, and the LLM cache and call log. One file, no server, no ORM."""
import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "crewasis.db"
DATA = Path(__file__).parent / "data"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    prefix TEXT PRIMARY KEY,            -- id prefix of its rows: R, S, P, M, O
    name TEXT NOT NULL, file TEXT NOT NULL, description TEXT,
    collected_at TEXT, synthetic TEXT, confidence REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,                -- R014, S051, P04, M21, O02464
    source_prefix TEXT NOT NULL REFERENCES sources(prefix),
    raw TEXT NOT NULL,                  -- the CSV row as JSON, unchanged
    date TEXT
);
CREATE INDEX IF NOT EXISTS evidence_by_source ON evidence(source_prefix);
CREATE TABLE IF NOT EXISTS engagements (
    id INTEGER PRIMARY KEY,
    brand TEXT NOT NULL,
    problem TEXT NOT NULL,
    plan TEXT NOT NULL,                 -- JSON: lenses, products, questions, planned_by
    llm_mode TEXT NOT NULL,             -- "ollama:<model>" or "offline"
    status TEXT NOT NULL,               -- running · done · failed
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    engagement_id INTEGER NOT NULL REFERENCES engagements(id),
    id TEXT NOT NULL,                   -- F1…F13
    lens TEXT, category TEXT, title TEXT,
    magnitude REAL, change_pct REAL, confidence REAL,
    evidence_ids TEXT, computation TEXT,
    PRIMARY KEY (engagement_id, id)
);
CREATE TABLE IF NOT EXISTS play_matches (
    engagement_id INTEGER NOT NULL REFERENCES engagements(id),
    play_id TEXT NOT NULL, matched INTEGER NOT NULL, fact_ids TEXT, reason TEXT,
    PRIMARY KEY (engagement_id, play_id)
);
CREATE TABLE IF NOT EXISTS brief_sections (
    id INTEGER PRIMARY KEY,
    engagement_id INTEGER NOT NULL REFERENCES engagements(id),
    section TEXT NOT NULL, key TEXT NOT NULL DEFAULT '', fact_id TEXT, text TEXT NOT NULL, template TEXT NOT NULL,
    written_by TEXT NOT NULL,           -- llm · template (only when the LLM is off or unreachable)
    check_status TEXT NOT NULL,         -- passed · repaired · flagged · missing
    problem TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY,
    engagement_id INTEGER REFERENCES engagements(id),
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
    written_by TEXT DEFAULT 'template', -- who worded the action: llm · template (offline)
    flag TEXT DEFAULT '',               -- why the LLM's wording couldn't be verified, if it couldn't
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
CREATE TABLE IF NOT EXISTS llm_cache (
    key TEXT PRIMARY KEY, response TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY,
    engagement_id INTEGER,
    step TEXT NOT NULL,                 -- plan · synthesize · frame · handoff · ping
    model TEXT, ok INTEGER NOT NULL, cached INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER, error TEXT DEFAULT '', at TEXT NOT NULL
);
"""

# Files and the date column of each source, keyed by id prefix.
SOURCE_DATE_COLUMN = {"R": "date", "S": "date", "P": "date_checked", "M": "month", "O": "order_date"}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(path=None) -> sqlite3.Connection:
    con = sqlite3.connect(path or DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    # upgrade databases created before a column existed
    for table, column, ddl in (("cards", "flag", "TEXT DEFAULT ''"),
                               ("brief_sections", "key", "TEXT NOT NULL DEFAULT ''")):
        if column not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    return con


def _typed(value: str):
    """CSV values arrive as text; store numbers as numbers so JSON keeps their type."""
    if value == "":
        return ""
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    return value


def ensure_seeded(con, data_dir: Path = DATA) -> dict:
    """Load the synthetic CSVs into `sources` and `evidence` once. Returns rows per source."""
    if con.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0:
        with open(data_dir / "sources.csv", newline="") as f:
            sources = list(csv.DictReader(f))
        for s in sources:
            con.execute("INSERT OR REPLACE INTO sources VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (s["prefix"], s["name"], s["file"], s["description"], s["collected_at"],
                         s["synthetic"], float(s["confidence"])))
            date_col = SOURCE_DATE_COLUMN.get(s["prefix"])
            with open(data_dir / s["file"], newline="") as f:
                rows = [{k: _typed(v) for k, v in r.items()} for r in csv.DictReader(f)]
            con.executemany("INSERT INTO evidence (id, source_prefix, raw, date) VALUES (?, ?, ?, ?)",
                            [(r["id"], s["prefix"], json.dumps(r), str(r.get(date_col, ""))) for r in rows])
        con.commit()
    return {r["source_prefix"]: r["n"] for r in
            con.execute("SELECT source_prefix, COUNT(*) AS n FROM evidence GROUP BY source_prefix")}


def reset(con):
    """Clear every engagement and card; keep the loaded evidence and the LLM cache."""
    con.executescript("DELETE FROM card_events; DELETE FROM cards; DELETE FROM brief_sections; "
                      "DELETE FROM play_matches; DELETE FROM facts; DELETE FROM engagements; "
                      "DELETE FROM llm_calls;")
    con.commit()


# ------------------------------------------------------------------ engagements
def create_engagement(con, brand, problem, plan, llm_mode) -> int:
    cur = con.execute("INSERT INTO engagements (brand, problem, plan, llm_mode, status, created_at) "
                      "VALUES (?, ?, ?, ?, 'running', ?)", (brand, problem, json.dumps(plan), llm_mode, now()))
    return cur.lastrowid


def engagement(con, eid):
    r = con.execute("SELECT * FROM engagements WHERE id = ?", (eid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["plan"] = json.loads(d["plan"])
    return d


def engagements(con):
    return [dict(r) for r in con.execute("SELECT id, problem, llm_mode, status, created_at FROM engagements "
                                         "ORDER BY id DESC")]


def set_engagement_status(con, eid, status):
    con.execute("UPDATE engagements SET status = ? WHERE id = ?", (status, eid))


def save_facts(con, eid, facts):
    con.executemany("INSERT OR REPLACE INTO facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(eid, f.id, f.lens, f.category, f.title, f.magnitude, f.change_pct, f.confidence,
                      json.dumps(f.evidence_ids), json.dumps(f.computation, default=str)) for f in facts])


def save_play_matches(con, eid, plays):
    con.executemany("INSERT OR REPLACE INTO play_matches VALUES (?, ?, ?, ?, ?)",
                    [(eid, p.id, int(p.matched), json.dumps([p.fact_id] if p.fact_id else []), p.reason)
                     for p in plays])


def save_sentences(con, eid, sentences):
    con.execute("DELETE FROM brief_sections WHERE engagement_id = ?", (eid,))
    con.executemany("INSERT INTO brief_sections (engagement_id, section, key, fact_id, text, template, written_by, "
                    "check_status, problem) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(eid, s.section, s.key, s.fact_id, s.text, s.template, s.written_by, s.status, s.problem)
                     for s in sentences])


def sentences(con, eid):
    return [dict(r) for r in con.execute("SELECT * FROM brief_sections WHERE engagement_id = ? ORDER BY id", (eid,))]


# ------------------------------------------------------------------ cards
def log(con, card_id, event, from_role=None, to_role=None, from_state=None, to_state=None):
    con.execute("INSERT INTO card_events (card_id, event, from_role, to_role, from_state, to_state, at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", (card_id, event, from_role, to_role, from_state, to_state, now()))


def add_card(con, **c) -> int:
    t = now()
    cur = con.execute(
        "INSERT INTO cards (engagement_id, fact_id, play_id, kind, parent_card_id, category, owner_role, state, base, "
        "relevance_score, evidence, suggested_action, written_by, requires_approval, gate_rule, note, created_at, "
        "updated_at, flag) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (c.get("engagement_id"), c["fact_id"], c.get("play_id"), c["kind"], c.get("parent_card_id"), c["category"],
         c["owner_role"], c["state"], c["base"], c["relevance_score"], c["evidence"], c["suggested_action"],
         c.get("written_by", "template"), int(bool(c.get("requires_approval"))), c.get("gate_rule"),
         c.get("note", ""), t, t, c.get("flag", "")))
    log(con, cur.lastrowid, "created", to_role=c["owner_role"], to_state=c["state"])
    return cur.lastrowid


def update_card(con, card_id, **fields):
    fields["updated_at"] = now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    con.execute(f"UPDATE cards SET {sets} WHERE id = ?", (*fields.values(), card_id))


def cards(con, kind=None, engagement_id=None):
    q, where, args = "SELECT * FROM cards", [], []
    if kind:
        where.append("kind = ?"); args.append(kind)
    if engagement_id is not None:
        where.append("engagement_id = ?"); args.append(engagement_id)
    if where:
        q += " WHERE " + " AND ".join(where)
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


# ------------------------------------------------------------------ LLM cache and log
def cache_get(con, key):
    r = con.execute("SELECT response FROM llm_cache WHERE key = ?", (key,)).fetchone()
    return r["response"] if r else None


def cache_put(con, key, response):
    con.execute("INSERT OR REPLACE INTO llm_cache VALUES (?, ?, ?)", (key, response, now()))
    con.commit()


def log_llm_call(con, step, model, ok, cached, latency_ms, error="", engagement_id=None):
    con.execute("INSERT INTO llm_calls (engagement_id, step, model, ok, cached, latency_ms, error, at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (engagement_id, step, model, int(ok), int(cached), latency_ms, error[:500], now()))
    con.commit()


def llm_calls(con, engagement_id=None):
    q, args = "SELECT * FROM llm_calls", ()
    if engagement_id is not None:
        q, args = q + " WHERE engagement_id = ?", (engagement_id,)
    return [dict(r) for r in con.execute(q + " ORDER BY id", args)]
