#!/usr/bin/env python3
"""Imaginarium v0 — text-only. No images anywhere by design.

    python cli.py char new   --world "Between the Stations"
    python cli.py loc new    --world "Between the Stations"
    python cli.py session new --world "Between the Stations"
    python cli.py play 1
"""
import argparse
import json
import sys

import db
import llm
import play


def _pick(rows, label, fmt=lambda r: r["name"]):
    if not rows:
        print(f"no {label} yet.")
        return None
    for i, r in enumerate(rows, 1):
        print(f"  {i}. {fmt(r)}")
    while True:
        raw = input(f"{label} #> ").strip()
        if not raw:
            return None
        try:
            n = int(raw)
            if 1 <= n <= len(rows):
                return rows[n - 1]
        except ValueError:
            pass
        print("  ?")


def _multiline(prompt):
    print(prompt)
    print("(blank line to finish)")
    lines = []
    while True:
        line = input("  ")
        if not line.strip():
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _ensure_relationships(conn, world_id, char_ids, ask=True):
    """Generate the missing pairwise history/friction records for a cast."""
    import creation

    pairs = db.relationship_pairs_missing(conn, world_id, char_ids)
    if not pairs:
        return 0
    if ask:
        n = len(pairs)
        a = input(f"\n{n} pair(s) have no history between them. Write it? [Y/n] ")
        if a.strip().lower() not in ("", "y"):
            return 0
    made = 0
    for a_id, b_id in pairs:
        ca, cb = db.character_get(conn, a_id), db.character_get(conn, b_id)
        print(f"  [{ca['name']} \u2194 {cb['name']} \u2026]")
        try:
            spec = creation.make_relationship(ca, cb)
        except Exception as e:
            print(f"  \033[31m{e}\033[0m")
            continue
        db.relationship_pair_insert(conn, world_id, a_id, b_id, spec)
        if spec.get("friction"):
            print(f"    \033[2mfriction:\033[0m {spec['friction']}")
        made += 1
    return made


def _pair_records(conn, chosen):
    """Deduplicated history/friction rows among a chosen cast."""
    ids = [c["id"] for c in chosen]
    seen, out = set(), []
    for c in chosen:
        for r in db.relationships_from(conn, c["id"], [i for i in ids if i != c["id"]]):
            k = (r["history"], r["friction"])
            if k not in seen:
                seen.add(k)
                out.append(r)
    return out


# ---------- commands ----------

def cmd_char_new(args):
    import creation

    conn = db.init()
    wid = db.world_get_or_create(conn, args.world)

    desc = _multiline("Describe the character:")
    if not desc:
        print("nothing to do.")
        return

    tags = input("Style tags (e.g. @kantoku, blank for none): ").strip()
    style_tags = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    cast = db.character_list(conn, wid)
    if cast:
        names = ", ".join(c["name"] for c in cast)
        print(f"\n[expanding against the existing cast: {names}…]")
    else:
        print("\n[expanding…]")
    spec = creation.make_character(desc, cast=cast, style_tags=style_tags)

    print(f"\n\033[1m{spec['name']}\033[0m")
    print(f"\n{spec['bio']}\n")
    print("PERSONA")
    print(spec["persona_prompt"])
    print("\nOUTFITS")
    for o in spec["outfits"]:
        mark = " (default)" if o.get("default") else ""
        print(f"  - {o['name']}{mark}")

    if input("\nKeep? [Y/n] ").strip().lower() in ("", "y"):
        cid = db.character_insert(conn, wid, spec, source_desc=desc)
        print(f"saved as character {cid}")
        ids = [c["id"] for c in db.character_list(conn, wid)]
        if len(ids) > 1:
            _ensure_relationships(conn, wid, ids)
    else:
        print("discarded.")


def cmd_char_list(args):
    conn = db.init()
    wid = db.world_get_or_create(conn, args.world)
    for c in db.character_list(conn, wid):
        outs = ", ".join(o["name"] for o in db.outfits_for(conn, c["id"]))
        print(f"{c['id']:>3}  {c['name']}  [{outs}]")


def cmd_char_show(args):
    conn = db.init()
    c = db.character_get(conn, args.id)
    if not c:
        print("no such character")
        return
    print(f"\033[1m{c['name']}\033[0m\n\n{c['bio']}\n")
    print("PERSONA\n" + c["persona_prompt"] + "\n")
    print("VOICE\n" + json.dumps(json.loads(c["voice"] or "{}"), indent=2))
    rels = db.relationships_from(conn, c["id"])
    if rels:
        print("\nRELATIONSHIPS")
        for r in rels:
            print(f"  -> {r['to_name']}: wants {r['wants']}")
            if r["friction"]:
                print(f"     unresolved: {r['friction']}")
    print("\nAPPEARANCE\n" + (c["appearance"] or "(none)"))


def cmd_loc_new(args):
    import creation

    conn = db.init()
    wid = db.world_get_or_create(conn, args.world)
    desc = _multiline("Describe the location:")
    if not desc:
        return
    print("\n[expanding…]")
    spec = creation.make_location(desc)
    print(f"\n\033[1m{spec['name']}\033[0m\n\n{spec['description']}\n")
    if spec.get("camera_contract"):
        print(f"CAMERA  {spec['camera_contract']}")
    for st in spec.get("staging", []):
        print(f"  mark  {st.get('id')}  ({st.get('pose_class')})  {st.get('note','')}")
    if input("\nKeep? [Y/n] ").strip().lower() in ("", "y"):
        lid = db.location_insert(conn, wid, spec)
        print(f"saved as location {lid}")


def cmd_loc_list(args):
    conn = db.init()
    wid = db.world_get_or_create(conn, args.world)
    for l in db.location_list(conn, wid):
        print(f"{l['id']:>3}  {l['name']}")


def cmd_session_new(args):
    conn = db.init()
    wid = db.world_get_or_create(conn, args.world)

    chars = db.character_list(conn, wid)
    if not chars:
        print("create some characters first.")
        return

    chosen = []
    print("\nCharacters — enter numbers to toggle, blank line when done:")
    while True:
        for i, c in enumerate(chars, 1):
            mark = " \033[32m✓\033[0m" if c["id"] in [x["id"] for x in chosen] else "  "
            print(f" {mark} {i}. {c['name']}")
        raw = input("# > ").strip()
        if not raw:
            break
        try:
            n = int(raw)
            if not 1 <= n <= len(chars):
                raise ValueError
        except ValueError:
            print("  ?")
            continue
        c = chars[n - 1]
        if c["id"] in [x["id"] for x in chosen]:
            chosen = [x for x in chosen if x["id"] != c["id"]]
        else:
            chosen.append(c)

    if not chosen:
        print("need at least one character.")
        return
    print("cast: " + ", ".join(c["name"] for c in chosen))

    print("\nLocation:")
    loc = _pick(db.location_list(conn, wid), "location")
    if loc is None:
        print("need a location.")
        return

    if len(chosen) > 1:
        _ensure_relationships(conn, wid, [c["id"] for c in chosen])

    premise = input("\nOpening premise (blank to write one): ").strip()
    opening = ""
    if not premise:
        import creation
        print("[writing the situation…]")
        try:
            spec = creation.make_premise(chosen, _pair_records(conn, chosen), loc)
            premise = spec.get("premise", "").strip()
            opening = spec.get("opening_beat", "").strip()
            print(f"\n{premise}\n")
            if opening:
                print(f"\033[38;5;179mNarrator: {opening}\033[0m\n")
            if input("Keep? [Y/n] ").strip().lower() not in ("", "y"):
                premise = input("Opening premise: ").strip()
                opening = ""
        except Exception as e:
            print(f"\033[31m  {e}\033[0m")
            premise = input("Opening premise: ").strip()

    sid = db.session_create(conn, wid, loc["id"], premise,
                            [c["id"] for c in chosen])
    if opening:
        db.turn_append(conn, sid, "Narrator", opening, "ai")
    print(f"\nsession {sid} created. run:  python cli.py play {sid}")


def cmd_models(args):
    for name, size in llm.list_models():
        gb = size / 1e9
        mark = " *" if name == llm.MODEL else ""
        print(f"  {name:<40} {gb:>6.1f} GB{mark}")
    print(f"\ncurrent: {llm.MODEL}")
    print("override with --model <tag> or IMAGINARIUM_MODEL=<tag>")


def cmd_session_list(args):
    conn = db.init()
    for s in db.session_list(conn):
        print(f"{s['id']:>3}  {s['world_name']} / {s['location_name']}  "
              f"({s['turns']} turns)  {s['premise'][:50]}")


HELP = """
  <name> <text>     speak as that character (prefix match on first name)
  /n <text>         narrator beat
  /ai [name]        generate a line; no name = next in rotation
  /auto <n>         generate n turns, alternating speakers
  /cast             list participants
  /add [name]       add a character; no arg lists who's available
  /drop <name>      remove a character from the session
  /t                replay transcript
  /undo             remove last turn
  /temp <float>     sampling temperature (default 0.85)
  /model [tag]      swap model mid-session; no arg lists installed
  /export           write transcript to session-N.txt
  /q                quit
"""


def cmd_play(args):
    conn = db.init()
    sess = db.session_get(conn, args.session_id)
    if not sess:
        print("no such session")
        return

    loc = db.location_get(conn, sess["location_id"])
    cast = db.participants(conn, args.session_id)
    caches = llm.SpeakerCache()
    temp = 0.85
    rotation = 0

    try:
        resolved, _ = llm.load_model()
        llm.set_model(resolved)
    except RuntimeError as e:
        print(f"\n\033[31m{e}\033[0m")
        return

    def gen(c):
        """Generate one turn; returns None on failure instead of raising."""
        print(f"\033[1;38;5;168m{c['name']}:\033[0m ", end="", flush=True)
        try:
            return play.generate_turn(conn, args.session_id, c, caches, temp)
        except llm.OllamaError as e:
            print(f"\n\033[31m  {e}\033[0m")
            return None
        except KeyboardInterrupt:
            print("\n  [interrupted]")
            return None

    print(f"\n\033[1m{loc['name']}\033[0m")
    print(f"{sess['premise']}\n")
    print("cast: " + ", ".join(c["name"] for c in cast))
    print("/? for commands\n")

    for r in db.turns(conn, args.session_id):
        print(play.render_turn(r))
    print()

    def find_char(token):
        token = token.lower()
        for c in cast:
            if c["name"].lower().startswith(token) or \
               c["name"].split()[0].lower() == token:
                return c
        return None

    while True:
        try:
            raw = input("\033[2m> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not raw:
            continue

        if raw in ("/q", "/quit", "/exit"):
            break

        if raw in ("/?", "/help"):
            print(HELP)
            continue

        if raw.startswith("/add"):
            parts = raw.split(maxsplit=1)
            if len(parts) < 2:
                pool = db.character_list(conn, sess["world_id"])
                here = {c["id"] for c in cast}
                for c in pool:
                    if c["id"] not in here:
                        print(f"  {c['name']}")
                continue
            target = parts[1].strip().lower()
            pool = db.character_list(conn, sess["world_id"])
            here = {c["id"] for c in cast}
            match = next(
                (c for c in pool
                 if c["id"] not in here and
                 (c["name"].lower().startswith(target) or
                  c["name"].split()[0].lower() == target)),
                None,
            )
            if not match:
                print("  no matching character outside this session")
                continue
            outfit = conn.execute(
                "SELECT id FROM outfit WHERE character_id = ? "
                "ORDER BY is_default DESC, id LIMIT 1", (match["id"],)
            ).fetchone()
            conn.execute(
                "INSERT INTO participant (session_id, character_id, outfit_id) "
                "VALUES (?,?,?)",
                (args.session_id, match["id"], outfit["id"] if outfit else None),
            )
            conn.commit()
            cast = db.participants(conn, args.session_id)
            caches.invalidate()
            print(f"  + {match['name']}  (cast: "
                  f"{', '.join(c['name'] for c in cast)})")
            continue

        if raw.startswith("/drop"):
            parts = raw.split(maxsplit=1)
            if len(parts) < 2:
                print("  /drop <name>")
                continue
            c = find_char(parts[1].strip())
            if not c:
                print("  not in this session")
                continue
            if len(cast) <= 1:
                print("  can't drop the last character")
                continue
            conn.execute(
                "DELETE FROM participant WHERE session_id = ? AND character_id = ?",
                (args.session_id, c["id"]),
            )
            conn.commit()
            cast = db.participants(conn, args.session_id)
            caches.invalidate()
            rotation = 0
            print(f"  - {c['name']}  (cast: "
                  f"{', '.join(x['name'] for x in cast)})")
            continue

        if raw == "/cast":
            for c in cast:
                print(f"  {c['name']}")
            continue

        if raw == "/t":
            for r in db.turns(conn, args.session_id):
                print(play.render_turn(r))
            continue

        if raw == "/undo":
            gone = db.turn_pop(conn, args.session_id)
            caches.invalidate()
            if gone and gone["character_id"]:
                ids = [x["id"] for x in cast]
                if gone["character_id"] in ids:
                    rotation = ids.index(gone["character_id"])
            print(f"  removed: {gone['speaker']}: {gone['markup'][:60]}" if gone
                  else "  nothing to undo")
            continue

        if raw.startswith("/model"):
            parts = raw.split(maxsplit=1)
            if len(parts) > 1:
                try:
                    llm.load_model(parts[1].strip())
                    llm.set_model(parts[1].strip())
                    caches.invalidate()
                    print(f"  model = {llm.MODEL}")
                except RuntimeError as e:
                    print(f"  {e}")
            else:
                for name, size in llm.list_models():
                    mark = " *" if name == llm.MODEL else ""
                    print(f"  {name:<40} {size/1e9:>6.1f} GB{mark}")
            continue

        if raw.startswith("/temp"):
            try:
                temp = float(raw.split()[1])
                print(f"  temp = {temp}")
            except (IndexError, ValueError):
                print(f"  temp = {temp}")
            continue

        if raw == "/export":
            rows = db.turns(conn, args.session_id)
            path = f"session-{args.session_id}.txt"
            with open(path, "w") as f:
                f.write(f"{loc['name']}\n{sess['premise']}\n\n")
                for r in rows:
                    f.write(f"{r['speaker']}: {r['markup']}\n")
            print(f"  wrote {path}")
            continue

        if raw.startswith("/n "):
            text = raw[3:].strip()
            idx = db.turn_append(conn, args.session_id, "Narrator", text, "human")
            print(play.render_turn(db.turns(conn, args.session_id)[idx]))
            continue

        if raw.startswith("/auto"):
            try:
                n = int(raw.split()[1])
            except (IndexError, ValueError):
                n = 4
            for _ in range(n):
                c = cast[rotation % len(cast)]
                rotation += 1
                line = gen(c)
                if line is None:
                    break
                db.turn_append(conn, args.session_id, c["name"], line, "ai", c["id"])
                play.maybe_summarize(conn, args.session_id)
            continue

        if raw.startswith("/ai"):
            parts = raw.split(maxsplit=1)
            if len(parts) > 1:
                c = find_char(parts[1].strip())
                if not c:
                    print("  no such character in this session")
                    continue
            else:
                c = cast[rotation % len(cast)]
            rotation = [x["id"] for x in cast].index(c["id"]) + 1
            line = gen(c)
            if line is None:
                continue
            db.turn_append(conn, args.session_id, c["name"], line, "ai", c["id"])
            play.maybe_summarize(conn, args.session_id)
            continue

        # "name rest of line" -> speak manually
        head, _, rest = raw.partition(" ")
        c = find_char(head)
        if c and rest.strip():
            idx = db.turn_append(conn, args.session_id, c["name"], rest.strip(),
                                 "human", c["id"])
            print(play.render_turn(db.turns(conn, args.session_id)[idx]))
            rotation = [x["id"] for x in cast].index(c["id"]) + 1
            continue

        print("  ? — /? for commands")


def main():
    p = argparse.ArgumentParser(prog="imaginarium")
    p.add_argument("--model", default=None,
                   help="Ollama model tag; overrides IMAGINARIUM_MODEL")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("models", help="list installed models").set_defaults(fn=cmd_models)

    def add_world(sp):
        sp.add_argument("--world", default="Default World")

    ch = sub.add_parser("char").add_subparsers(dest="sub", required=True)
    add_world(ch.add_parser("new")); ch.choices["new"].set_defaults(fn=cmd_char_new)
    add_world(ch.add_parser("list")); ch.choices["list"].set_defaults(fn=cmd_char_list)
    show = ch.add_parser("show"); show.add_argument("id", type=int)
    show.set_defaults(fn=cmd_char_show)

    lo = sub.add_parser("loc").add_subparsers(dest="sub", required=True)
    add_world(lo.add_parser("new")); lo.choices["new"].set_defaults(fn=cmd_loc_new)
    add_world(lo.add_parser("list")); lo.choices["list"].set_defaults(fn=cmd_loc_list)

    se = sub.add_parser("session").add_subparsers(dest="sub", required=True)
    add_world(se.add_parser("new")); se.choices["new"].set_defaults(fn=cmd_session_new)
    se.add_parser("list").set_defaults(fn=cmd_session_list)

    pl = sub.add_parser("play")
    pl.add_argument("session_id", type=int)
    pl.set_defaults(fn=cmd_play)

    args = p.parse_args()
    if getattr(args, "model", None):
        llm.set_model(args.model)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
