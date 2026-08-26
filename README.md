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

**Qwen3 thinking is disabled** via `/no_think` in the system prompt, and
`<think>` blocks are stripped defensively if that's ignored. Set
`IMAGINARIUM_NO_THINK=0` to re-enable, but one-line generation and the
JSON creation step both break with it on.

## Use

```bash
python cli.py char new    --world "Between the Stations"
python cli.py char new    --world "Between the Stations"
python cli.py loc new     --world "Between the Stations"
python cli.py session new --world "Between the Stations"
python cli.py play 1
```

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
person — what they want, what they conceal, how they act under pressure.
Bios written for reading make bland personas. If characters start
sounding alike, this field is the cause, not the model.

**Visual fields are written but unread.** `appearance`,
`prompt_fragment`, `renderer`, `style_tags` cost one LLM call now and a
migration later.

**The markup contract is enforced from turn one.** `<action>` in angle
brackets, `*bold*`, `_italic_`. `clean_line()` rewrites asterisk-actions
into angle brackets and strips a leading `Name:` if the model adds one
anyway. Keep this strict — the classifier parses it later.

## What to watch for

- **Voice convergence.** Twenty turns in, cover the names. Can you tell
  who's speaking? If not, rewrite personas before touching anything else.
- **Action inflation.** The model will tag every line with a gesture.
  The format rules push against it; if it persists, that's a signal the
  Stage Manager will need the transient/postural distinction badly.
- **Both characters agreeing.** Real friction needs the personas to want
  incompatible things. If scenes resolve too smoothly, that's a persona
  problem, not a topology problem.

## Next

Stage Manager: constrained decode over the sprite inventory, classifying
each action as transient / postural / wardrobe / positional / affective.
Build it against exported transcripts before wiring any image generation.
