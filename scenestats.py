#!/usr/bin/env python3
"""Score a session on the failure modes the first voice test exposed.

    python3 scenestats.py [session_id ...]

Reports, per speaker: how many distinct ways they open a line, their most
repeated opening, and the share of their turns carrying an action tag. The
headline number is DISTINCT OPENINGS — template lock shows up there long
before it shows up in your impression of the transcript.
"""
import sys
import db
import play


def stats(conn, sid):
    sess = db.session_get(conn, sid)
    if not sess:
        print(f"no session {sid}")
        return
    loc = db.location_get(conn, sess["location_id"])
    rows = db.turns(conn, sid)
    ai = [r for r in rows if r["origin"] == "ai" and r["speaker"] != "Narrator"]

    print(f"\n\033[1msession {sid}\033[0m  {loc['name']}  "
          f"{len(rows)} turns ({len(ai)} generated)")
    if not ai:
        return

    tagged = sum(1 for r in ai if play.has_action(r["markup"]))
    print(f"  action rate        {tagged}/{len(ai)}  "
          f"({100 * tagged // len(ai)}%)   [was 100% in the first run]")

    compound = sum(1 for r in ai if play.has_action(r["markup"])
                   and "," in r["markup"].split(">")[0])
    if tagged:
        print(f"  compound actions   {compound}/{tagged}")

    speakers = {}
    for r in ai:
        speakers.setdefault(r["speaker"], []).append(r["markup"])

    print()
    worst = 0
    for name, lines in speakers.items():
        print(f"  {name}")
        # Two measures, because they catch different locks. A fixed bigram
        # ("I observe ...") shows up in the 2-word key. A fixed prefix
        # ("Analyzing: ..." / "Logging: ...") varies at word two but is just
        # as locked, and only the 1-word key sees it.
        for label, n_words in (("first word ", 1), ("first two ", 2)):
            keys = [play.opening_key(m, n_words) for m in lines]
            uniq = len(set(keys))
            top = max(set(keys), key=keys.count)
            n = keys.count(top)
            worst = max(worst, n / len(lines))
            flag = "\033[31m" if uniq <= max(2, len(lines) // 4) else "\033[32m"
            print(f"    distinct {label}   {flag}{uniq}/{len(lines)}\033[0m"
                  f"   most repeated \"{top}…\" x{n}")

    verdict = ("\033[31mTEMPLATE LOCK\033[0m" if worst >= 0.6 else
               "\033[33mdrifting\033[0m" if worst >= 0.35 else
               "\033[32mno lock\033[0m")
    print(f"\n  verdict            {verdict} "
          f"(top opening covers {int(worst * 100)}% of one speaker's lines)")

    # cross-speaker mirroring: an opening used by more than one character
    allkeys = {n: set(play.opening_key(m) for m in ls) for n, ls in speakers.items()}
    shared = set.intersection(*allkeys.values()) if len(allkeys) > 1 else set()
    shared = {k for k in shared if k}
    if shared:
        print(f"  \033[31mshared openings\033[0m    {', '.join(sorted(shared))}")


if __name__ == "__main__":
    conn = db.init()
    ids = [int(a) for a in sys.argv[1:]] or [s["id"] for s in db.session_list(conn)]
    for sid in ids:
        stats(conn, sid)
    print()
