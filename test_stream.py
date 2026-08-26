"""Offline tests for llm.stream_line buffering, driven with scripted Ollama
events. No Ollama, no network.

    python3 test_stream.py
"""
import sys, os, json, io, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm

class FakeResp:
    def __init__(self, chunks):
        self.lines = [json.dumps({"message": {"content": c}}).encode() for c in chunks]
        self.lines.append(json.dumps({"message": {"content": ""}, "done": True}).encode())
    def __iter__(self): return iter(self.lines)
    def __enter__(self): return self
    def __exit__(self, *a): return False

FAIL = []
def run(name, chunks, want):
    llm._post_chat = lambda *a, **k: FakeResp(chunks)
    got = "".join(llm.stream_line(("s", "u"), "k", None))
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f"  got {got!r} want {want!r}"))
    if not ok: FAIL.append(name)

print("\nstream_line buffering")
run("leading newline does not end the turn",
    ["\n", "Hello", " there", "\n", "trailing"], "Hello there")
run("plain single line", ["One line only"], "One line only")
run("think block then the answer",
    ["<think>", "weighing it up", "</think>", "The real line."], "The real line.")
run("think block split mid-tag",
    ["<th", "ink>hmm", "</think>", "Answer here"], "Answer here")
run("newline inside a think block is not a stop",
    ["<think>a\nb\nc</think>", "After the block"], "After the block")
run("cut at the first newline after content",
    ["Visible text", "\n", "second line"], "Visible text")
run("REGRESSION visible text then a think block still yields the visible text",
    ["Hello", "<think>late</think>", " world"], "Hello world")
run("whitespace-only prelude", ["  ", "\n", " Real"], "Real")

print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
