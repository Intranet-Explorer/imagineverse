"""Prompt assembly and turn generation.

Block order is system -> transcript -> persona, persona LAST. Ollama's runner
keeps the previous request's KV cache and matches the longest common prefix, so
alternating speakers only re-prefill the persona tail. Do not reorder these.

The persona tail now also carries the speaker's relationships, because they are
speaker-specific and would break the shared prefix if they sat in the system
block. The rolling summary sits at the top of the user block: it changes only
every SUMMARIZE_EVERY turns, so most turns still hit the cache.
"""
import json
import os
import re

import db
import llm

# How many turns are sent verbatim, and how much slack before we re-summarise.
WINDOW = int(os.environ.get("IMAGINARIUM_WINDOW", "24"))
SUMMARIZE_EVERY = int(os.environ.get("IMAGINARIUM_SUMMARIZE_EVERY", "12"))

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

ACTIONS MUST EARN THEIR PLACE:
- Include an action ONLY when the body does something the words do not say.
    <looks away> I'm fine.                      KEEP - body contradicts the line
    <smiles> That's funny.                      CUT - body repeats the line
    <checks the panel> I'm checking the panel.  CUT - pure restatement
- If the action and the dialogue carry the same information, drop the action
  and write the line alone. Most lines should have no action at all.

DO NOT MIRROR:
- Do not begin your line the way you began your last one.
- Do not adopt the other speaker's sentence shape, prefixes, or phrasing.
  If every line they write starts the same way, yours must not.
"""

SUMMARY_SYSTEM = """You compress the transcript of a scene.

Return plain prose, no JSON, no preamble, under 150 words, present tense.
Cover: what has actually happened, what has changed between the people in it,
and what is still unresolved. Keep proper names. Do not invent events that are
not in the transcript, and do not editorialise about the characters."""


# ---------------------------------------------------------------- prompt

def _voice_block(speaker_row):
    try:
        voice = json.loads(speaker_row["voice"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return ""
    bits = []
    if voice.get("register"):
        bits.append(f"Register: {voice['register']}")
    avoids = voice.get("avoids") or voice.get("tics")
    if avoids:
        if isinstance(avoids, str):
            avoids = [avoids]
        bits.append("Never: " + "; ".join(avoids))
    return ("\n" + "\n".join(bits)) if bits else ""


def _relationship_block(conn, speaker_row, cast):
    others = [c["id"] for c in cast if c["id"] != speaker_row["id"]]
    rows = db.relationships_from(conn, speaker_row["id"], others)
    if not rows:
        return ""
    out = []
    for r in rows:
        seg = [f"\nBETWEEN YOU AND {r['to_name'].upper()}"]
        if r["history"]:
            seg.append(r["history"])
        if r["friction"]:
            seg.append(f"Unresolved: {r['friction']}")
        if r["wants"]:
            seg.append(f"What you want from them: {r['wants']}")
        if r["withholds"]:
            seg.append(f"What you will not say first: {r['withholds']}")
        out.append("\n".join(seg))
    return "\n" + "\n".join(out)


def _transcript(conn, session_id, sess):
    """Rolling summary plus the recent window, instead of the whole log."""
    upto = sess["summary_upto"] if "summary_upto" in sess.keys() else 0
    summary = sess["summary"] if "summary" in sess.keys() else ""
    rows = db.turns_range(conn, session_id, upto)
    body = "\n".join(f"{r['speaker']}: {r['markup']}" for r in rows)
    if summary and upto:
        head = f"EARLIER IN THIS SCENE\n{summary}\n\nSINCE THEN\n"
        return head + (body or "(nothing yet)")
    return body or "(the scene has not started yet)"


def build_prompt(conn, session_id, speaker_row, extra=""):
    """Assemble the prompt. Stable content first, speaker-specific content last."""
    sess = db.session_get(conn, session_id)
    loc = db.location_get(conn, sess["location_id"])
    cast = db.participants(conn, session_id)

    # --- system: stable for the whole session ---
    cast_lines = [f"- {c['name']}: {c['bio']}" for c in cast]

    system = f"""You are writing dialogue in an interactive scene.

SETTING - {loc['name']}
{loc['description']}

CAST
{chr(10).join(cast_lines)}

PREMISE
{sess['premise']}

{FORMAT_RULES}"""

    # --- user: transcript, then the speaker-specific tail ---
    transcript = _transcript(conn, session_id, sess)
    tail_extra = f"\n\n{extra}" if extra else ""

    user = f"""TRANSCRIPT SO FAR
{transcript}

---
You are now writing as {speaker_row['name']}.

{speaker_row['persona_prompt']}{_voice_block(speaker_row)}{_relationship_block(conn, speaker_row, cast)}
{tail_extra}
Write {speaker_row['name']}'s next line only."""

    return llm.chat_wrap(system, user)


# ---------------------------------------------------------------- summarising

def maybe_summarize(conn, session_id, verbose=True):
    """Fold the oldest turns into the summary once the window overflows."""
    sess = db.session_get(conn, session_id)
    if "summary_upto" not in sess.keys():
        return False
    upto = sess["summary_upto"]
    total = len(db.turns(conn, session_id))
    target = max(0, total - WINDOW)
    if target - upto < SUMMARIZE_EVERY:
        return False

    rows = db.turns_before(conn, session_id, target)
    body = "\n".join(f"{r['speaker']}: {r['markup']}" for r in rows)
    prior = sess["summary"]
    src = (f"SUMMARY SO FAR\n{prior}\n\nFULL TRANSCRIPT UP TO THIS POINT\n{body}"
           if prior else body)
    try:
        text = llm.complete(llm.chat_wrap(SUMMARY_SYSTEM, src),
                            max_tokens=400, temp=0.4).strip()
    except Exception as e:
        if verbose:
            print(f"\033[2m  [summary skipped: {e}]\033[0m")
        return False
    if not text:
        return False
    db.session_set_summary(conn, session_id, text, target)
    if verbose:
        print(f"\033[2m  [summarised turns 0-{target - 1}]\033[0m")
    return True


# ---------------------------------------------------------------- cleaning

_LEADING_NAME = re.compile(r"^\s*([A-Z][\w'’.-]*(?:\s+[A-Z][\w'’.-]*){0,3})\s*:\s*")
# A leading *...* is an action only if it reads like one: at least two words and
# opening on a lowercase verb. "*Never* again." is emphasis and must survive.
_LEADING_ASTERISK_ACTION = re.compile(r"^\*([a-z][^*]{2,118}?\s+[^*]+)\*\s+")


def clean_line(text, speaker_name):
    """Strip a leading 'Name:' the model added anyway, and tidy whitespace."""
    text = text.strip()
    m = _LEADING_NAME.match(text)
    if m and m.group(1).lower().startswith(speaker_name.split()[0].lower()):
        text = text[m.end():].strip()
    text = _LEADING_ASTERISK_ACTION.sub(r"<\1> ", text)
    return text.strip()


_ACTION_TAG = re.compile(r"^\s*<[^>]*>\s*")
_WORD = re.compile(r"[a-z0-9']+")


def opening_key(markup, n=2):
    """First n words of the spoken part, for mirror detection."""
    spoken = _ACTION_TAG.sub("", markup or "").lower()
    words = _WORD.findall(spoken)[:n]
    return " ".join(words)


def has_action(markup):
    return bool(_ACTION_TAG.match(markup or ""))


# ---------------------------------------------------------------- generation

ANTI_MIRROR = ("Your last lines in this scene opened with \"{key}\". Do not "
               "begin this line that way, and do not reuse that sentence shape. "
               "Open differently.")


def generate_turn(conn, session_id, speaker_row, caches, temp=0.85, stream=True,
                  anti_mirror=True):
    def once(extra, t):
        prompt = build_prompt(conn, session_id, speaker_row, extra=extra)
        key = f"s{session_id}:c{speaker_row['id']}"
        parts = []
        for chunk in llm.stream_line(prompt, key, caches, temp=t):
            parts.append(chunk)
            if stream:
                print(chunk, end="", flush=True)
        if stream:
            print()
        return clean_line("".join(parts), speaker_row["name"])

    line = once("", temp)
    if not anti_mirror or not line:
        return line

    # Resample once if this speaker has opened the same way twice already.
    recent = [r["markup"] for r in db.recent_by_speaker(conn, session_id,
                                                        speaker_row["id"], limit=2)]
    key = opening_key(line)
    if key and len(recent) >= 2 and all(opening_key(m) == key for m in recent):
        if stream:
            print(f"\033[2m  [\"{key}...\" for the third time - resampling]\033[0m")
        line = once(ANTI_MIRROR.format(key=key), min(1.15, temp + 0.15)) or line
    return line


# ---------------------------------------------------------------- rendering

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
