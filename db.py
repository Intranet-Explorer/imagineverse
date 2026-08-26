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


def init():
    conn = connect()
    conn.executescript(SCHEMA.read_text())
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
        """INSERT INTO location (world_id, name, description, prompt_fragment)
           VALUES (?,?,?,?)""",
        (
            world_id,
            spec["name"],
            spec.get("description", ""),
            spec.get("prompt_fragment", ""),
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
