"""Prompt assembly and turn generation."""
import json
import re

import db
import llm

FORMAT_RULES = """OUTPUT FORMAT - follow exactly:

Write exactly ONE line. The line is:

    <action> dialogue text

Rules:
- Physical actions go in angle brackets: <leans back in the chair>
- Actions describe only what a camera could see. No thoughts, no feelings.
- *asterisks* for emphasis, _underscores_ for softness. Never use asterisks
  for actions.
- Do not write the speaker's name - it is added automatically.
- Do not write any other character's dialogue or actions.
- Do not narrate. Do not summarise. One line, then stop.
- Most lines should have no action tag at all. Only include one when the
  body actually does something.
"""


def build_prompt(conn, session_id, speaker_row):
    """Assemble the prompt. Stable content first, persona last."""
    sess = db.session_get(conn, session_id)
    loc = db.location_get(conn, sess["location_id"])
    cast = db.participants(conn, session_id)

    # --- system: stable for the whole session ---
    cast_lines = []
    for c in cast:
        cast_lines.append(f"- {c['name']}: {c['bio']}")

    system = f"""You are writing dialogue in an interactive scene.

SETTING - {loc['name']}
{loc['description']}

CAST
{chr(10).join(cast_lines)}

PREMISE
{sess['premise']}

{FORMAT_RULES}"""

    # --- user: transcript (append-only), then persona (short tail) ---
    rows = db.turns(conn, session_id)
    if rows:
        transcript = "\n".join(f"{r['speaker']}: {r['markup']}" for r in rows)
    else:
        transcript = "(the scene has not started yet)"

    voice = {}
    try:
        voice = json.loads(speaker_row["voice"] or "{}")
    except json.JSONDecodeError:
        pass

    voice_bits = []
    if voice.get("register"):
        voice_bits.append(f"Register: {voice['register']}")
    if voice.get("tics"):
        voice_bits.append("Verbal habits: " + "; ".join(voice["tics"]))
    voice_block = ("\n" + "\n".join(voice_bits)) if voice_bits else ""

    user = f"""TRANSCRIPT SO FAR
{transcript}

---
You are now writing as {speaker_row['name']}.

{speaker_row['persona_prompt']}{voice_block}

Write {speaker_row['name']}'s next line only."""

    return llm.chat_wrap(system, user)


_LEADING_NAME = re.compile(r"^\s*([A-Z][\w'’.-]*(?:\s+[A-Z][\w'’.-]*){0,3})\s*:\s*")


def clean_line(text, speaker_name):
    """Strip a leading 'Name:' the model added anyway, and tidy whitespace."""
    text = text.strip()
    m = _LEADING_NAME.match(text)
    if m and m.group(1).lower().startswith(speaker_name.split()[0].lower()):
        text = text[m.end():].strip()
    # collapse stray asterisk-actions into angle brackets
    text = re.sub(r"^\*([^*]{3,120})\*\s+", r"<\1> ", text)
    return text.strip()


def generate_turn(conn, session_id, speaker_row, caches, temp=0.85, stream=True):
    prompt = build_prompt(conn, session_id, speaker_row)
    key = f"s{session_id}:c{speaker_row['id']}"

    parts = []
    for chunk in llm.stream_line(prompt, key, caches, temp=temp):
        parts.append(chunk)
        if stream:
            print(chunk, end="", flush=True)
    if stream:
        print()

    return clean_line("".join(parts), speaker_row["name"])


def render_turn(row):
    """Colourise a stored turn for the terminal."""
    C_NAME = "\033[1;38;5;168m"
    C_ACT = "\033[38;5;140m"
    C_NARR = "\033[38;5;179m"
    R = "\033[0m"

    if row["speaker"] == "Narrator":
        return f"{C_NARR}Narrator: {row['markup']}{R}"

    body = re.sub(r"<([^>]*)>", lambda m: f"{C_ACT}<{m.group(1)}>{R}", row["markup"])
    return f"{C_NAME}{row['speaker']}:{R} {body}"
