"""Character and location creation: one description in, structured record out."""
import json
import re

import llm

CHARACTER_SYSTEM = """You are the Narrator of an interactive fiction engine.
You turn a short description into a complete character record.

Return ONLY a JSON object. No preamble, no markdown fences, no commentary.

Schema:
{
  "name": "Full Name",
  "bio": "2-3 sentences of prose the *user* reads. Evocative, third person.",
  "persona_prompt": "The instruction block the *model* receives when speaking
     as this character. This is NOT the bio. Write it in second person
     ('You are...'). It must cover: what they want in a scene, what they
     conceal, how they behave under pressure, what they refuse to do, and
     how they treat other people. Concrete behavioural directives, not
     adjectives. 120-200 words.",
  "voice": {
    "register": "how they speak - diction, sentence length, formality",
    "tics": ["specific verbal habits, 2-4 items"]
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

The persona_prompt is the most important field. A weak persona produces a
character indistinguishable from every other character. Give them a
specific way of wanting things and a specific way of avoiding them."""

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
     the room, its furnishings, lighting, time of day, mood"
}"""


def _extract_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object found in output:\n{text[:400]}")
    return json.loads(text[start : end + 1])


def _generate_record(system, description, retries=2):
    last = None
    for attempt in range(retries + 1):
        prompt = llm.chat_wrap(system, description)
        raw = llm.complete(prompt, max_tokens=1600, temp=0.75 if attempt == 0 else 0.5)
        try:
            return _extract_json(raw)
        except (ValueError, json.JSONDecodeError) as e:
            last = e
            print(f"  [malformed JSON, retrying: {e}]")
    raise RuntimeError(f"could not get valid JSON after {retries + 1} attempts: {last}")


def make_character(description, renderer="anima", style_tags=None):
    spec = _generate_record(CHARACTER_SYSTEM, description)
    spec["renderer"] = renderer
    spec["style_tags"] = style_tags or []
    if not spec.get("outfits"):
        spec["outfits"] = [
            {"id": "default", "name": "Default", "prompt_fragment": "", "default": True}
        ]
    if not any(o.get("default") for o in spec["outfits"]):
        spec["outfits"][0]["default"] = True
    return spec


def make_location(description):
    return _generate_record(LOCATION_SYSTEM, description)
