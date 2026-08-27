#!/usr/bin/env python3
"""Score a session on the failure modes the first voice test exposed.

    python3 scenestats.py [session_id ...]

Reports, per speaker: how many distinct ways they open a line, their most
repeated opening, and the share of their turns carrying an action tag. The
headline number is DISTINCT OPENINGS — template lock shows up there long
before it shows up in your impression of the transcript.
"""
import os
import sqlite3
import sys

import db
import play


def read_only():
    """Open the database without writing to it.

    Deliberately NOT db.init(): that runs migrations, and a reporting tool has
    no business altering the file it is reporting on. (It also fails outright
    on some network/virtual filesystems, where a half-applied ALTER leaves a
    hot journal behind and the database refuses to open at all.)
    """
    path = os.environ.get("IMAGINARIUM_DB", "imaginarium.db")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


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

    # Openings are only one kind of lock. The second run kept its openings
    # varied and still degenerated into trading "X is Y" definitions, each
    # line picking up the noun the last one ended on. Measure that too, or
    # this script hands out a false pass.
    lines = [play.spoken(r["markup"]) for r in ai]
    cop, ch, ct = play.copula_rate(lines)
    car, rh, rt = play.carryover_rate(lines)

    print()
    cflag = "\033[31m" if cop >= 0.45 else "\033[33m" if cop >= 0.25 else "\033[32m"
    rflag = "\033[31m" if car >= 0.45 else "\033[33m" if car >= 0.25 else "\033[32m"
    print(f"  \"X is Y\" sentences   {cflag}{ch}/{ct}  ({int(cop*100)}%)\033[0m")
    print(f"  picks up the prior")
    print(f"  line's last word     {rflag}{rh}/{rt}  ({int(car*100)}%)\033[0m")

    if len(ai) >= 16:
        q = len(ai) // 4
        by_q = [play.copula_rate(lines[i*q:(i+1)*q])[0] for i in range(4)]
        print("  copula by quarter    "
              + "  ".join(f"{int(v*100)}%" for v in by_q)
              + ("   \033[31m<- getting worse\033[0m"
                 if by_q[3] > by_q[0] + 0.15 else ""))

    shape = (cop + car) / 2
    verdict = ("\033[31mTEMPLATE LOCK\033[0m" if worst >= 0.6 else
               "\033[31mSHAPE LOCK\033[0m" if shape >= 0.45 else
               "\033[33mdrifting\033[0m" if worst >= 0.35 or shape >= 0.25 else
               "\033[32mno lock\033[0m")
    print(f"\n  verdict            {verdict}")
    print(f"    openings           top covers {int(worst * 100)}% of one speaker's lines")
    print(f"    shape              {int(shape * 100)}%")

    # cross-speaker mirroring: an opening used by more than one character
    allkeys = {n: set(play.opening_key(m) for m in ls) for n, ls in speakers.items()}
    shared = set.intersection(*allkeys.values()) if len(allkeys) > 1 else set()
    shared = {k for k in shared if k}
    if shared:
        print(f"  \033[31mshared openings\033[0m    {', '.join(sorted(shared))}")


if __name__ == "__main__":
    conn = read_only()
    ids = [int(a) for a in sys.argv[1:]] or [s["id"] for s in db.session_list(conn)]
    for sid in ids:
        stats(conn, sid)
    print()
