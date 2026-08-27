"""Character, relationship, location and premise generation.

One description in, structured record out — but never in isolation. A character
generated with no knowledge of the cast it is joining converges on the same
archetype as the last one, and two personas written against an implied (absent)
human partner produce polite status updates when cast against each other. Every
generator here takes the surrounding world as input.
"""
import json
import re

import llm

# ---------------------------------------------------------------- characters

CHARACTER_SYSTEM = """You are the Narrator of an interactive fiction engine.
You turn a short description into a complete character record.

Return ONLY a JSON object. No preamble, no markdown fences, no commentary.

Schema:
{
  "name": "Full Name",
  "bio": "2-3 sentences of prose the *user* reads. Evocative, third person.",
  "persona_prompt": "The instruction block the *model* receives when speaking
     as this character. This is NOT the bio. Write it in second person
     ('You are...'). 120-200 words. It must cover, concretely:
       - what you want out of any conversation, stated as an outcome you are
         working toward, not a trait
       - the specific social move you make when someone will not give you that:
         deflect, flatter, needle, go quiet, change the subject, make it a joke,
         restate the demand louder. Name ONE and make it particular.
       - one thing you are wrong about and will defend anyway, AND the
         specific thing that would make you doubt it. A character who cannot
         be moved at all produces a stalemate, not a scene.
       - what you will not admit, and what it would cost you to admit it
     Behavioural directives, not adjectives.",
  "voice": {
    "register": "diction, sentence length, formality - how they sound",
    "avoids": ["2-4 things this character never does linguistically:
       never swears, never asks a direct question, never finishes a sentence
       about their family, never uses a word longer than two syllables"]
  },
  "appearance": "Physical description for illustration: build, hair, face,
     bearing. 1-2 sentences. Visual only.",
  "outfits": [
    {"id": "snake_case_slug",
     "name": "Readable Name",
     "prompt_fragment": "comma-separated visual tags for the garments only",
     "default": true}
  ]
}

Give 2-3 outfits appropriate to who they are. Exactly one has default: true.

HARD CONSTRAINTS - these exist because of observed failures:

1. NO CATCHPHRASES. Do not give this character a word or phrase they prefix
   their lines with, a logging format, a verbal signature, or any sentence-
   initial template. Characters given these lock onto them within two turns
   and stop being characters. `avoids` is a list of prohibitions, never a
   list of habits to perform.

2. NO STOCK FAILURE MODES. "Freezes and repeats the last input", "goes
   silent and processes", "deflects with humour" as a bare phrase - these are
   defaults the model reaches for and they come out identical across
   characters. The social move you name must be something only this person
   would do.

3. The character must be ABLE TO BE WRONG and must want something a
   reasonable person could refuse them. A character who only wants to be
   helpful generates no scene.

The persona_prompt is the most important field. A weak persona produces a
character indistinguishable from every other character. Give them a specific
way of wanting things and a specific way of not getting them."""

CAST_PREAMBLE = """This character is joining a world that already contains:

{cast}

Your character must COLLIDE with at least one of them: want something that
person will not give, hold a position they cannot accept, or need something
that costs that person to provide. Name which one in the persona_prompt and
say what the collision is. Do not produce a character who would get along
with everyone listed above.

Also: do not repeat their register, their profession, or their kind. If the
cast is all machines, this one is not a machine unless the description below
insists on it."""

# ---------------------------------------------------------------- relationships

RELATIONSHIP_SYSTEM = """You are the Narrator of an interactive fiction engine.
Given two characters, you define what stands between them.

Return ONLY a JSON object. No preamble, no markdown fences, no commentary.

Schema:
{
  "history": "2-3 sentences. Something that already happened between these two,
     specific and dated-feeling. Not a summary of their personalities. An
     event with a place and a consequence that is still in effect.",
  "friction": "One sentence. The disagreement neither has resolved. It must be
     something both of them believe they are right about.",
  "a_wants_from_b": "What A is trying to get out of B in any scene they share.
     Concrete and refusable.",
  "a_withholds": "What A will not say first, and what saying it would cost.",
  "b_wants_from_a": "Same, from B's side. It must NOT be the mirror image of
     a_wants_from_b - if both want the same thing the scene resolves in three
     lines.",
  "b_withholds": "What B will not say first, and what it would cost.",
  "a_concedes": "The scene's exit. Name the specific thing B could say, do or
     admit that would actually move A off their position - and what it costs A
     to be moved. Not 'A never concedes'. Something real and reachable inside
     one conversation.",
  "b_concedes": "The same for B, and it must be a different kind of thing than
     a_concedes."
}

The point of this record is that a scene between these two has somewhere to go
before anyone speaks. If your friction could be settled by one person simply
explaining themselves, it is too weak - rewrite it.

But a stalemate is not drama either. An observed failure: friction written as
"neither will concede" produced eighty turns in which nothing moved, because
nothing COULD. With position frozen the only variable left is intensity, so
the scene escalated physically instead of developing. Both characters must
have a reachable exit even if neither takes it."""

# ---------------------------------------------------------------- premise

PREMISE_SYSTEM = """You are the Narrator of an interactive fiction engine.
You write the opening situation for a scene.

Return ONLY a JSON object. No preamble, no markdown fences, no commentary.

Schema:
{
  "premise": "2-3 sentences, present tense. Why these people are in THIS ROOM
     RIGHT NOW, and what is unresolved. It must contain something that gets
     worse if nobody speaks - a deadline, someone about to leave, a decision
     that defaults badly, a thing already said that cannot be taken back.",
  "opening_beat": "One Narrator line to start the scene. A physical fact about
     the room or the moment. No dialogue, no interiority, under 20 words."
}

Do NOT write a premise that is only a setting and an activity. 'They meet in a
lab and test equipment' gives the characters nothing to want. Something must
already be wrong.

Use the LOCATION you are given. Do not invent a different room, and do not
move the scene somewhere more convenient. An observed failure: given a control
room full of server racks, the premise invented a green room and a recording
studio, and the scene spent eighty turns in a space that was never described.
The furniture named in the location is the furniture the characters can reach.

Do NOT resolve the friction in advance. Setting up a document that only needs
signing, or an ultimatum with one obvious answer, railroads the scene into a
single move. Give them a pressure, not a script."""

# ---------------------------------------------------------------- locations

LOCATION_SYSTEM = """You are the Narrator of an interactive fiction engine.
You turn a short description into a location record.

Return ONLY a JSON object. No preamble, no markdown fences, no commentary.

Schema:
{
  "name": "Location Name",
  "description": "3-5 sentences. What the space is, how it feels to be in it,
     what is physically present - furniture, light, sound, sightlines.
     Concrete enough that a scene can be staged in it.",
  "prompt_fragment": "comma-separated visual tags for illustration:
     the room, its furnishings, lighting, time of day, mood",
  "camera_contract": "A single fixed camera description that every character
     illustration for this location will be generated against: eye height,
     lens, subject distance, key light direction. One line, no variation.",
  "staging": [
    {"id": "snake_case_slug",
     "pose_class": "seated | standing | leaning",
     "note": "where in the room, and who would naturally be there"}
  ]
}

Give 2-3 staging positions, no more. A location is a stage, not a map - two or
three marks is what a scene actually uses."""


def _extract_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object found in output:\n{text[:400]}")
    return json.loads(text[start : end + 1])


def _generate_record(system, description, retries=2, max_tokens=1600):
    last = None
    for attempt in range(retries + 1):
        prompt = llm.chat_wrap(system, description)
        raw = llm.complete(prompt, max_tokens=max_tokens,
                           temp=0.85 if attempt == 0 else 0.6)
        try:
            return _extract_json(raw)
        except (ValueError, json.JSONDecodeError) as e:
            last = e
            print(f"  [malformed JSON, retrying: {e}]")
    raise RuntimeError(f"could not get valid JSON after {retries + 1} attempts: {last}")


def _cast_line(c):
    """One line per existing character, enough to collide with."""
    want = (c["persona_prompt"] or "").strip().replace("\n", " ")
    if len(want) > 260:
        want = want[:260].rsplit(" ", 1)[0] + "..."
    return f"- {c['name']}: {want}"


def make_character(description, cast=(), renderer="anima", style_tags=None):
    """cast is the existing characters in this world (sqlite3.Row or dicts)."""
    system = CHARACTER_SYSTEM
    if cast:
        system = (system + "\n\n"
                  + CAST_PREAMBLE.format(cast="\n".join(_cast_line(c) for c in cast)))
    spec = _generate_record(system, description)
    spec["renderer"] = renderer
    spec["style_tags"] = style_tags or []

    # a model that ignores the no-catchphrase rule usually does it here
    voice = spec.get("voice") or {}
    if "tics" in voice and "avoids" not in voice:
        voice["avoids"] = voice.pop("tics")
    spec["voice"] = voice

    if not spec.get("outfits"):
        spec["outfits"] = [
            {"id": "default", "name": "Default", "prompt_fragment": "", "default": True}
        ]
    if not any(o.get("default") for o in spec["outfits"]):
        spec["outfits"][0]["default"] = True
    return spec


def make_relationship(a, b):
    desc = (
        f"CHARACTER A - {a['name']}\n{a['bio']}\n{a['persona_prompt']}\n\n"
        f"CHARACTER B - {b['name']}\n{b['bio']}\n{b['persona_prompt']}"
    )
    return _generate_record(RELATIONSHIP_SYSTEM, desc, max_tokens=900)


def make_premise(cast, rels, location):
    lines = [f"LOCATION - {location['name']}", location["description"], "", "CAST"]
    for c in cast:
        lines.append(f"- {c['name']}: {c['bio']}")
    if rels:
        lines += ["", "BETWEEN THEM"]
        for r in rels:
            if r["history"]:
                lines.append(f"- {r['history']}")
            if r["friction"]:
                lines.append(f"  unresolved: {r['friction']}")
    return _generate_record(PREMISE_SYSTEM, "\n".join(lines), max_tokens=700)


def make_location(description):
    spec = _generate_record(LOCATION_SYSTEM, description)
    if not spec.get("staging"):
        spec["staging"] = [
            {"id": "centre", "pose_class": "standing", "note": "middle of the room"}
        ]
    return spec
