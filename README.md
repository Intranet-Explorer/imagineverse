# Imaginarium v0

Text only. No images, no sprites, no ComfyUI. The point of v0 is to
answer one question: **do per-character personas produce genuinely
different voices, or does everyone converge?**

The transcripts you generate here become the test corpus for the Stage
Manager classifier later.

## Setup

Ollama backend. No pip installs — stdlib only.

```bash
ollama list                     # find your exact model tag
export IMAGINARIUM_MODEL="qwen3.8:27b-mlx"   # the default; --model overrides
export IMAGINARIUM_DB="./imaginarium.db"
export IMAGINARIUM_CTX=16384
```

**`IMAGINARIUM_CTX` matters.** Ollama defaults to a 4096-token context and
silently truncates past it — a long transcript loses its head with no
error. 16k is fine on 64GB; raise it if sessions run long.

**Qwen3 thinking is disabled** via `"think": false` in the request body —
reasoning arrives as a separate `thinking` field, so a `/no_think` prompt
directive is the wrong control and is only used as a fallback on builds that
reject the parameter. `<think>` blocks are stripped defensively either way.
Set `IMAGINARIUM_NO_THINK=0` to re-enable, but one-line generation and the
JSON creation step both break with it on.

**Two more knobs.** `IMAGINARIUM_WINDOW` (default 24) is how many turns are
sent verbatim before the rest is folded into a rolling summary;
`IMAGINARIUM_SUMMARIZE_EVERY` (default 12) is how much slack before
re-summarising. Set `IMAGINARIUM_WINDOW` very high to disable summarising.

## Tests

```bash
python3 test_offline.py    # migration, relationships, prompts, summary, loop-breaker
python3 test_stream.py     # stream_line buffering
```

Both run against a stub model. No Ollama, no network, no GPU.

## Use

```bash
python cli.py char new    --world "Between the Stations"
python cli.py char new    --world "Between the Stations"   # sees the first
python cli.py loc new     --world "Between the Stations"
python cli.py session new --world "Between the Stations"   # blank premise = generated
python cli.py play 1
```

The second `char new` is shown the existing cast and must produce someone who
collides with them. After saving, it offers to write the history and the
unresolved friction between each pair. Leaving the premise blank at
`session new` generates a situation that gets worse if nobody speaks, plus an
opening Narrator beat.

In `play`:

```
vivienne  <taps the desk> Then let's start.   speak manually
/ai marina                                    generate one line
/ai                                           next in rotation
/auto 8                                       eight alternating turns
/n The lights flicker.                        narrator beat
/undo   /t   /temp 0.9   /export   /q
```

Prefix match on first name, so `viv` works.

## Design notes

**Persona sits last in the prompt.** System block (world, cast, format)
is stable; transcript is append-only; the persona block is a short tail.
Ollama's runner keeps the previous request's KV cache and matches the
longest common prefix of the next one — so alternating speakers only
re-prefills the persona tail, a few hundred tokens, instead of the whole
context. This is free as long as the ordering holds. Don't reorder these
three blocks.

**`bio` and `persona_prompt` are different fields on purpose.** Bio is
prose the user reads. Persona is behavioural instruction in second
person — what they want, what they conceal, the specific social move they
make when refused. Bios written for reading make bland personas.

**But a good persona is not sufficient.** The first voice test had two
specific, well-opposed personas and still produced two interchangeable
speakers, because each was written in isolation against an implied absent
human. Characters are now generated with the cast in view and must collide
with someone already in the world, and every pair gets a `relationship` row:
what each wants from the other, what each will not say first, and the
disagreement neither has resolved. A scene needs somewhere to go before
anyone speaks.

**No catchphrases.** `voice.tics` became `voice.avoids` — prohibitions
rather than habits. A character handed a sentence-initial template locks onto
it within two turns and stops being a character. This is not a style
preference; it is the observed failure mode.

**Visual fields are written but unread.** `appearance`,
`prompt_fragment`, `renderer`, `style_tags` cost one LLM call now and a
migration later.

**The markup contract is enforced from turn one.** `<action>` in angle
brackets, `*bold*`, `_italic_`. `clean_line()` rewrites asterisk-actions
into angle brackets and strips a leading `Name:` if the model adds one
anyway. Keep this strict — the classifier parses it later. A leading
`*emphasis*` is *not* an action and must survive intact; a fabricated tag
poisons the classifier's corpus.

**An action must earn its place.** Include one only when the body does
something the words do not say. `<looks away> I'm fine.` keeps.
`<smiles> That's funny.` cuts. This replaced a quota ("most lines should have
no action"), which the model cannot reason about mid-generation and which it
ignored on 20 of 20 turns.

## What to watch for

- **Template lock.** This, not voice blur, is what the first run produced:
  every line from a character opening the same way. Check the *first two
  words* of each speaker's lines, not whether you can tell them apart — two
  stuck sentence generators are perfectly distinguishable and neither is a
  character. The anti-mirror resample catches a third repeat; if it fires
  often, the format rules are not doing their job.
- **Action inflation.** Was 100% of turns before the earn-its-place rule.
  Anything near that again means the rule is being ignored too.
- **Both characters agreeing.** Real friction needs the personas to want
  incompatible things — which is now the relationship's job, not the
  persona's. If scenes resolve smoothly, read the `friction` field: if one
  person simply explaining themselves would settle it, it was too weak.
- **Callbacks that stop landing.** The rolling summary is lossy. If a
  character starts failing to reference something they said thirty turns ago,
  raise `IMAGINARIUM_WINDOW`.

## Next

Stage Manager: constrained decode over the sprite inventory, classifying
each action as transient / postural / wardrobe / positional / affective.
Build it against exported transcripts before wiring any image generation.
