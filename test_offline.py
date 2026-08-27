"""Offline regression tests: exercises schema migration, relationships, prompt
assembly, the rolling summary and the anti-mirror resample against a stub llm.

    python3 test_offline.py

No Ollama, no network, no model. Runs on a throwaway copy of imaginarium.db if
one exists, so it never touches live data."""
import os, sys, json, types, shutil, tempfile

SRC = os.path.dirname(os.path.abspath(__file__))
os.environ["IMAGINARIUM_WINDOW"] = "6"
os.environ["IMAGINARIUM_SUMMARIZE_EVERY"] = "2"

# ---- fake llm, installed before creation/play import it ----
calls = []
class Stub(types.ModuleType):
    scripted = {}
    def chat_wrap(self, system, user): return (system, user)
    def complete(self, bundle, max_tokens=1600, temp=0.7):
        sysmsg = bundle[0]
        calls.append(("complete", sysmsg.split("\n")[0][:40]))
        for needle, payload in self.scripted.items():
            if needle in sysmsg:
                return payload
        return "SUMMARY: they argued about the key."
    def stream_line(self, bundle, key, caches, max_tokens=220, temp=0.85):
        calls.append(("stream", temp))
        yield self.next_line
    class SpeakerCache:
        def prepare(self, k, t): return None, t
        def invalidate(self, k=None): pass
    class OllamaError(RuntimeError): pass
    def load_model(self, n=None): return (n or "stub", None)
    def set_model(self, n): return n
    def list_models(self): return [("stub", 1)]

stub = Stub("llm"); stub.next_line = "x"
sys.modules["llm"] = stub
sys.path.insert(0, SRC)

import db, play, creation

FAIL = []
def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  " + str(detail)))
    if not cond: FAIL.append(name)

# ============================ 1. migration on the REAL database ==========
tmp = tempfile.mkdtemp()
live = os.path.join(tmp, "live.db")
_real = os.path.join(SRC, "imaginarium.db")
HAVE_LIVE = os.path.exists(_real) and os.path.getsize(_real) > 0
if HAVE_LIVE:
    shutil.copy(_real, live)
os.environ["IMAGINARIUM_DB"] = live
import importlib; importlib.reload(db)

print("\n[1] migration against the existing database")
conn = db.init()
cols = lambda t: [r["name"] for r in conn.execute(f"PRAGMA table_info({t})")]
check("session.summary added", "summary" in cols("session"))
check("session.summary_upto added", "summary_upto" in cols("session"))
check("location.camera_contract added", "camera_contract" in cols("location"))
check("location.staging added", "staging" in cols("location"))
check("relationship table created", "relationship" in
      [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")])
N_TURNS = len(db.turns(conn, 1)) if HAVE_LIVE else 0
if HAVE_LIVE and N_TURNS:
    check("existing turns intact", N_TURNS > 0, N_TURNS)
    check("existing characters intact", len(db.character_list(conn, 1)) >= 1)
    db.init()  # idempotent second run
    check("init() is idempotent", len(db.turns(conn, 1)) == N_TURNS)
else:
    print("  skip  live-database checks (no populated imaginarium.db)")

# ============================ 2. relationships ===========================
print("\n[2] relationship round-trip")
if not (HAVE_LIVE and len(db.character_list(conn, 1)) >= 2):
    print("  skip  (needs a world with two characters)")
    chars = None
else:
    chars = db.character_list(conn, 1)
if chars:
    a, b = chars[0], chars[1]
    db.relationship_pair_insert(conn, 1, a["id"], b["id"], {
        "history": "They were both present when the west array failed.",
        "friction": "Echo logged it as operator error. Pip did not correct the log.",
        "a_wants_from_b": "an admission that the log was wrong",
        "a_withholds": "that it filed the report knowing Pip would be blamed",
        "b_wants_from_a": "to be left off the incident record entirely",
        "b_withholds": "that it has already requested a transfer",
    })
    ra = db.relationships_from(conn, a["id"], [b["id"]])
    rb = db.relationships_from(conn, b["id"], [a["id"]])
    check("both directions written", len(ra) == 1 and len(rb) == 1)
    check("wants are not mirrored", ra[0]["wants"] != rb[0]["wants"])
    check("history is symmetric", ra[0]["history"] == rb[0]["history"])
    check("no missing pairs now", db.relationship_pairs_missing(conn, 1, [a["id"], b["id"]]) == [])
    db.relationship_pair_insert(conn, 1, a["id"], b["id"], {"history": "h2", "friction": "f2",
        "a_wants_from_b": "w", "a_withholds": "x", "b_wants_from_a": "y", "b_withholds": "z"})
    check("upsert does not duplicate",
          len(db.relationships_from(conn, a["id"], [b["id"]])) == 1)

    # ============================ 3. clean_line ==============================
    print("\n[3] clean_line")
    cases = [
        ("*leans in* Tell me.",        "<leans in> Tell me.",  "asterisk action converts"),
        ("*Never* again.",             "*Never* again.",       "leading emphasis survives"),
        ("*Stop.* I mean it.",         "*Stop.* I mean it.",   "single-word emphasis survives"),
        ("Echo: <nods> Fine.",         "<nods> Fine.",         "own name stripped"),
        ("Marina: Don't.",             "Marina: Don't.",       "other name kept"),
    ]
    for src, want, label in cases:
        got = play.clean_line(src, "Echo")
        check(label, got == want, f"{src!r} -> {got!r} (wanted {want!r})")

    # ============================ 4. opening_key =============================
    print("\n[4] opening_key")
    check("strips the action tag",
          play.opening_key("<tilts head> I observe a stable wave") == "i observe")
    check("bare dialogue", play.opening_key("Logging: no fault") == "logging no")
    check("differs across openings",
          play.opening_key("I observe x") != play.opening_key("Analyzing: x"))

    # ============================ 5. prompt assembly =========================
    print("\n[5] prompt assembly and the cache invariant")
    sysA, userA = play.build_prompt(conn, 1, a)
    sysB, userB = play.build_prompt(conn, 1, b)
    check("persona is the tail", userA.rstrip().endswith("next line only."))
    check("transcript precedes persona",
          userA.index("TRANSCRIPT SO FAR") < userA.index("You are now writing as"))
    check("relationship block is in the tail",
          "BETWEEN YOU AND" in userA and
          userA.index("You are now writing as") < userA.index("BETWEEN YOU AND"))
    check("system block identical across speakers", sysA == sysB)
    shared = os.path.commonprefix([userA, userB])
    check("user blocks share the transcript prefix",
          len(shared) > userA.index("You are now writing as") - 5,
          f"shared={len(shared)} tail starts at {userA.index('You are now writing as')}")
    check("format rules ask for earned actions", "MUST EARN THEIR PLACE" in sysA)
    check("format rules forbid mirroring", "DO NOT MIRROR" in sysA)
    check("voice renders avoids as prohibitions",
          "Never:" in userA or "avoids" not in (a["voice"] or ""))

    # ============================ 6. summarisation ===========================
    print("\n[6] rolling summary")
    before = db.session_get(conn, 1)["summary_upto"]
    fired = play.maybe_summarize(conn, 1, verbose=False)
    after = db.session_get(conn, 1)
    check("summary fired past the window", fired and after["summary_upto"] > before,
          f"{before} -> {after['summary_upto']}")
    check("summary text stored", bool(after["summary"]))
    check("summary covers total-WINDOW", after["summary_upto"] == 20 - 6,
          after["summary_upto"])
    s2, u2 = play.build_prompt(conn, 1, a)
    check("prompt now sends the summary", "EARLIER IN THIS SCENE" in u2)
    check("prompt shrank", len(u2) < len(userA), f"{len(userA)} -> {len(u2)}")
    check("does not re-fire immediately", not play.maybe_summarize(conn, 1, verbose=False))

    # ============================ 7. loop-breaker ============================

print("\n[7] anti-mirror resample")
fresh = os.path.join(tmp, "fresh.db")
os.environ["IMAGINARIUM_DB"] = fresh
importlib.reload(db); importlib.reload(play)
c2 = db.init()
w = db.world_get_or_create(c2, "W")
cid_a = db.character_insert(c2, w, {"name": "Ada", "bio": "b", "persona_prompt": "p",
    "voice": {"register": "clipped", "avoids": ["never swears"]},
    "outfits": [{"id": "o", "name": "O", "default": True}]})
cid_b = db.character_insert(c2, w, {"name": "Bo", "bio": "b", "persona_prompt": "p",
    "outfits": [{"id": "o", "name": "O", "default": True}]})
lid = db.location_insert(c2, w, {"name": "Room", "description": "d",
    "camera_contract": "eye level, 35mm", "staging": [{"id": "m", "pose_class": "standing"}]})
sid = db.session_create(c2, w, lid, "premise", [cid_a, cid_b])
loc = db.location_get(c2, lid)
check("camera_contract persisted", loc["camera_contract"] == "eye level, 35mm")
check("staging persisted as json", json.loads(loc["staging"])[0]["id"] == "m")

ada = db.character_get(c2, cid_a)
db.turn_append(c2, sid, "Ada", "I observe the door.", "ai", cid_a)
db.turn_append(c2, sid, "Ada", "I observe the lock.", "ai", cid_a)
calls.clear()
stub.next_line = "I observe the window."
line = play.generate_turn(c2, sid, ada, stub.SpeakerCache(), stream=False)
streams = [c for c in calls if c[0] == "stream"]
check("third identical opening triggers a resample", len(streams) == 2, streams)
check("resample raises temperature", len(streams) == 2 and streams[1][1] > streams[0][1])

calls.clear()
stub.next_line = "The door is open."
play.generate_turn(c2, sid, ada, stub.SpeakerCache(), stream=False)
check("a different opening does not resample",
      len([c for c in calls if c[0] == "stream"]) == 1)

# ============================ 8. cast-aware creation =====================
print("\n[8] cast-aware creation")
stub.scripted = {"You are the Narrator of an interactive fiction engine.":
    json.dumps({"name": "Cy", "bio": "b", "persona_prompt": "p",
                "voice": {"register": "flat", "tics": ["says noted"]},
                "outfits": [{"id": "a", "name": "A"}]})}
seen = {}
_orig = stub.complete
def spy(bundle, max_tokens=1600, temp=0.7):
    seen["system"] = bundle[0]
    return _orig(bundle, max_tokens, temp)
stub.complete = spy
spec = creation.make_character("a courier", cast=db.character_list(c2, w))
check("cast is injected into the prompt", "Ada" in seen["system"] and "Bo" in seen["system"])
check("collision is demanded", "COLLIDE" in seen["system"])
check("catchphrases are forbidden", "NO CATCHPHRASES" in seen["system"])
check("legacy tics rewritten to avoids",
      "avoids" in spec["voice"] and "tics" not in spec["voice"], spec["voice"])
check("default outfit forced", spec["outfits"][0]["default"] is True)
creation.make_character("a courier", cast=())
check("no cast block when world is empty", "COLLIDE" not in seen["system"])
stub.complete = _orig

# ============================ 9. shape metrics ===========================
print("\n[9] shape-lock metrics")
locked = ["Proof is a log. The log is closed.",
          "A closed log is a loop. The loop has no decay.",
          "Decay is a variable. The variable is a glitch.",
          "A glitch is a loop. The loop is a sample."]
free = ["The tremor is a syncopation error. Your printout is wrong.",
        "You have two minutes to sign before the log timestamps your refusal.",
        "Four milliseconds is mechanical. You are conflating hardware with variance.",
        "I played the bridge at six-five. You flattened it."]
check("copula rate flags the locked exchange", play.copula_rate(locked)[0] >= 0.6,
      play.copula_rate(locked))
check("copula rate clears the live one", play.copula_rate(free)[0] < 0.35,
      play.copula_rate(free))
check("carryover flags the locked exchange", play.carryover_rate(locked)[0] >= 0.6,
      play.carryover_rate(locked))
check("carryover clears the live one", play.carryover_rate(free)[0] < 0.4,
      play.carryover_rate(free))
check("stall_score is high when locked", play.stall_score(locked) >= 0.6)
check("stall_score is low when live", play.stall_score(free) < 0.4)
check("stall_score ignores too-short runs", play.stall_score(locked[:2]) == 0.0)
check("spoken() drops the action tag",
      play.spoken("<nods slowly> The grid is noise.") == "The grid is noise.")
check("strip_action drops only the leading tag",
      play.strip_action("<nods> a <b> c") == "a <b> c")

# ============================ 10. action run cap =========================
print("\n[10] action run cap and stall nudge")
sid2 = db.session_create(c2, w, lid, "p", [cid_a, cid_b])
db.turn_append(c2, sid2, "Ada", "<nods> One.", "ai", cid_a)
g, forbid = play._guidance(c2, sid2, ada, False)
check("one tagged line trips the cap (ACTION_RUN=1)", forbid, (g[:40], forbid))
check("the note is in the guidance", play.NO_ACTION_NOTE in g)

stub.next_line = "<shrugs> Two."
got = play.generate_turn(c2, sid2, ada, stub.SpeakerCache(), stream=False)
check("tag stripped when the model ignores it", got == "Two.", got)

db.turn_append(c2, sid2, "Ada", got, "ai", cid_a)
g2, forbid2 = play._guidance(c2, sid2, ada, False)
check("cap releases after an untagged line", not forbid2)

sid3 = db.session_create(c2, w, lid, "p", [cid_a, cid_b])
for t in ["Proof is a log. The log is closed.",
          "A closed log is a loop. The loop has no decay.",
          "Decay is a variable. The variable is a glitch.",
          "A glitch is a loop. The loop is a sample."]:
    db.turn_append(c2, sid3, "Ada", t, "ai", cid_a)
g3, _ = play._guidance(c2, sid3, ada, False)
check("stalled exchange gets the nudge", play.STALL_NOTE in g3)
sid4 = db.session_create(c2, w, lid, "p", [cid_a, cid_b])
for t in free:
    db.turn_append(c2, sid4, "Ada", t, "ai", cid_a)
g4, _ = play._guidance(c2, sid4, ada, False)
check("live exchange gets no nudge", play.STALL_NOTE not in g4)

# ============================ 11. the exit ===============================
print("\n[11] relationship exit")
db.relationship_pair_insert(c2, w, cid_a, cid_b, {
    "history": "h", "friction": "f",
    "a_wants_from_b": "aw", "a_withholds": "ax", "a_concedes": "if Bo names the date",
    "b_wants_from_a": "bw", "b_withholds": "bx", "b_concedes": "if Ada stops filming"})
r = db.relationships_from(c2, cid_a, [cid_b])[0]
check("concedes stored", r["concedes"] == "if Bo names the date", r["concedes"])
_, u = play.build_prompt(c2, sid, ada)
check("exit reaches the persona tail", "What would actually move you:" in u)
check("exit sits after the transcript",
      u.index("TRANSCRIPT SO FAR") < u.index("What would actually move you:"))

print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
