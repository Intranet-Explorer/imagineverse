"""SQLite access layer."""
import json
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("IMAGINARIUM_DB", "imaginarium.db"))
SCHEMA = Path(__file__).parent / "schema.sql"


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_column(conn, table, column, decl):
    """Add a column to an existing database. schema.sql CREATE TABLE statements
    are IF NOT EXISTS, so they never alter a table that already exists — new
    columns have to be migrated in explicitly."""
    have = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    if column not in have:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        return True
    return False


MIGRATIONS = [
    ("session", "summary", "TEXT NOT NULL DEFAULT ''"),
    ("session", "summary_upto", "INTEGER NOT NULL DEFAULT 0"),
    ("location", "camera_contract", "TEXT NOT NULL DEFAULT ''"),
    ("relationship", "concedes", "TEXT NOT NULL DEFAULT ''"),
    ("location", "staging", "TEXT NOT NULL DEFAULT '[]'"),
]


def init():
    conn = connect()
    conn.executescript(SCHEMA.read_text())
    for table, column, decl in MIGRATIONS:
        _ensure_column(conn, table, column, decl)
    conn.commit()
    return conn


# ---------- world ----------

def world_get_or_create(conn, name):
    row = conn.execute("SELECT * FROM world WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO world (name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid


def world_list(conn):
    return conn.execute("SELECT * FROM world ORDER BY name").fetchall()


# ---------- character ----------

def character_insert(conn, world_id, spec, source_desc=""):
    cur = conn.execute(
        """INSERT INTO character
           (world_id, name, bio, persona_prompt, voice, appearance,
            renderer, style_tags, source_desc)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            world_id,
            spec["name"],
            spec.get("bio", ""),
            spec.get("persona_prompt", ""),
            json.dumps(spec.get("voice", {})),
            spec.get("appearance", ""),
            spec.get("renderer", "anima"),
            json.dumps(spec.get("style_tags", [])),
            source_desc,
        ),
    )
    char_id = cur.lastrowid
    for i, o in enumerate(spec.get("outfits", [])):
        conn.execute(
            """INSERT INTO outfit
               (character_id, slug, name, prompt_fragment, is_default)
               VALUES (?,?,?,?,?)""",
            (
                char_id,
                o.get("id") or o.get("slug") or f"outfit_{i}",
                o.get("name", f"Outfit {i+1}"),
                o.get("prompt_fragment", ""),
                1 if o.get("default") or i == 0 else 0,
            ),
        )
    conn.commit()
    return char_id


def character_list(conn, world_id):
    return conn.execute(
        "SELECT * FROM character WHERE world_id = ? ORDER BY name", (world_id,)
    ).fetchall()


def character_by_name(conn, world_id, name):
    return conn.execute(
        "SELECT * FROM character WHERE world_id = ? AND name = ? COLLATE NOCASE",
        (world_id, name),
    ).fetchone()


def character_get(conn, char_id):
    return conn.execute("SELECT * FROM character WHERE id = ?", (char_id,)).fetchone()


def outfits_for(conn, char_id):
    return conn.execute(
        "SELECT * FROM outfit WHERE character_id = ? ORDER BY is_default DESC, id",
        (char_id,),
    ).fetchall()


# ---------- location ----------

def location_insert(conn, world_id, spec):
    cur = conn.execute(
        """INSERT INTO location
           (world_id, name, description, prompt_fragment, camera_contract, staging)
           VALUES (?,?,?,?,?,?)""",
        (
            world_id,
            spec["name"],
            spec.get("description", ""),
            spec.get("prompt_fragment", ""),
            spec.get("camera_contract", ""),
            json.dumps(spec.get("staging", [])),
        ),
    )
    conn.commit()
    return cur.lastrowid


def location_list(conn, world_id):
    return conn.execute(
        "SELECT * FROM location WHERE world_id = ? ORDER BY name", (world_id,)
    ).fetchall()


def location_get(conn, loc_id):
    return conn.execute("SELECT * FROM location WHERE id = ?", (loc_id,)).fetchone()


# ---------- session ----------

def session_create(conn, world_id, location_id, premise, character_ids):
    cur = conn.execute(
        "INSERT INTO session (world_id, location_id, premise) VALUES (?,?,?)",
        (world_id, location_id, premise),
    )
    sid = cur.lastrowid
    for cid in character_ids:
        default = conn.execute(
            "SELECT id FROM outfit WHERE character_id = ? ORDER BY is_default DESC, id LIMIT 1",
            (cid,),
        ).fetchone()
        conn.execute(
            "INSERT INTO participant (session_id, character_id, outfit_id) VALUES (?,?,?)",
            (sid, cid, default["id"] if default else None),
        )
    conn.commit()
    return sid


def session_get(conn, sid):
    return conn.execute("SELECT * FROM session WHERE id = ?", (sid,)).fetchone()


def session_list(conn):
    return conn.execute(
        """SELECT s.*, l.name AS location_name, w.name AS world_name,
                  (SELECT COUNT(*) FROM turn t WHERE t.session_id = s.id) AS turns
           FROM session s
           JOIN location l ON l.id = s.location_id
           JOIN world w ON w.id = s.world_id
           ORDER BY s.id DESC"""
    ).fetchall()


def participants(conn, sid):
    return conn.execute(
        """SELECT c.*, p.outfit_id
           FROM participant p JOIN character c ON c.id = p.character_id
           WHERE p.session_id = ? ORDER BY c.name""",
        (sid,),
    ).fetchall()


# ---------- turn ----------

def turns(conn, sid):
    return conn.execute(
        "SELECT * FROM turn WHERE session_id = ? ORDER BY idx", (sid,)
    ).fetchall()


def turn_append(conn, sid, speaker, markup, origin, character_id=None):
    row = conn.execute(
        "SELECT COALESCE(MAX(idx), -1) + 1 AS n FROM turn WHERE session_id = ?", (sid,)
    ).fetchone()
    conn.execute(
        """INSERT INTO turn (session_id, idx, speaker, character_id, markup, origin)
           VALUES (?,?,?,?,?,?)""",
        (sid, row["n"], speaker, character_id, markup, origin),
    )
    conn.commit()
    return row["n"]


def turn_pop(conn, sid):
    row = conn.execute(
        "SELECT * FROM turn WHERE session_id = ? ORDER BY idx DESC LIMIT 1", (sid,)
    ).fetchone()
    if row:
        conn.execute("DELETE FROM turn WHERE id = ?", (row["id"],))
        conn.commit()
    return row


# ---------- relationship ----------

def relationship_upsert(conn, world_id, from_id, to_id, wants, withholds,
                        history="", friction="", concedes=""):
    conn.execute(
        """INSERT INTO relationship
           (world_id, from_id, to_id, wants, withholds, history, friction, concedes)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(from_id, to_id) DO UPDATE SET
             wants=excluded.wants, withholds=excluded.withholds,
             history=excluded.history, friction=excluded.friction,
             concedes=excluded.concedes""",
        (world_id, from_id, to_id, wants, withholds, history, friction, concedes),
    )
    conn.commit()


def relationship_pair_insert(conn, world_id, a_id, b_id, spec):
    """Write both directions of one pair from a RELATIONSHIP_SYSTEM record.
    history and friction are symmetric and stored identically on both rows."""
    hist = spec.get("history", "")
    fric = spec.get("friction", "")
    relationship_upsert(conn, world_id, a_id, b_id,
                        spec.get("a_wants_from_b", ""),
                        spec.get("a_withholds", ""), hist, fric,
                        spec.get("a_concedes", ""))
    relationship_upsert(conn, world_id, b_id, a_id,
                        spec.get("b_wants_from_a", ""),
                        spec.get("b_withholds", ""), hist, fric,
                        spec.get("b_concedes", ""))


def relationships_from(conn, from_id, to_ids=None):
    """Rows describing how from_id stands toward each of to_ids."""
    if to_ids is not None and not to_ids:
        return []
    if to_ids is None:
        return conn.execute(
            """SELECT r.*, c.name AS to_name FROM relationship r
               JOIN character c ON c.id = r.to_id
               WHERE r.from_id = ? ORDER BY c.name""", (from_id,)
        ).fetchall()
    marks = ",".join("?" * len(to_ids))
    return conn.execute(
        f"""SELECT r.*, c.name AS to_name FROM relationship r
            JOIN character c ON c.id = r.to_id
            WHERE r.from_id = ? AND r.to_id IN ({marks}) ORDER BY c.name""",
        (from_id, *to_ids),
    ).fetchall()


def relationship_pairs_missing(conn, world_id, char_ids):
    """Unordered pairs among char_ids with no relationship row yet."""
    have = set()
    for r in conn.execute(
        "SELECT from_id, to_id FROM relationship WHERE world_id = ?", (world_id,)
    ):
        have.add((r["from_id"], r["to_id"]))
    out = []
    ids = sorted(char_ids)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if (a, b) not in have or (b, a) not in have:
                out.append((a, b))
    return out


# ---------- session summary ----------

def session_set_summary(conn, sid, summary, upto):
    conn.execute("UPDATE session SET summary = ?, summary_upto = ? WHERE id = ?",
                 (summary, upto, sid))
    conn.commit()


def turns_range(conn, sid, start_idx):
    """Turns from start_idx onward, in order."""
    return conn.execute(
        "SELECT * FROM turn WHERE session_id = ? AND idx >= ? ORDER BY idx",
        (sid, start_idx),
    ).fetchall()


def turns_before(conn, sid, end_idx):
    return conn.execute(
        "SELECT * FROM turn WHERE session_id = ? AND idx < ? ORDER BY idx",
        (sid, end_idx),
    ).fetchall()


def recent_by_speaker(conn, sid, character_id, limit=3):
    return conn.execute(
        """SELECT markup FROM turn
           WHERE session_id = ? AND character_id = ? AND origin = 'ai'
           ORDER BY idx DESC LIMIT ?""",
        (sid, character_id, limit),
    ).fetchall()
