# Imaginarium v0

Text only. No images, sprites or ComfyUI. v0 answers one question: **do
per-character personas produce distinct voices, or does everyone converge?**

Transcripts from here become the test corpus for the Stage Manager classifier.

## Setup

Ollama backend, stdlib only.

```bash
ollama list                                  # find your model tag
export IMAGINARIUM_MODEL="qwen3.8:27b-mlx"   # default; --model overrides
export IMAGINARIUM_DB="./imaginarium.db"
export IMAGINARIUM_CTX=16384
```

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server |
| `IMAGINARIUM_MODEL` | `qwen3.8:27b-mlx` | Model tag |
| `IMAGINARIUM_DB` | `imaginarium.db` | SQLite file |
| `IMAGINARIUM_CTX` | `16384` | Context size. Ollama defaults to 4096 and silently truncates past it. |
| `IMAGINARIUM_NO_THINK` | `1` | Disable Qwen3 reasoning. One-line generation and JSON creation break with it on. |
| `IMAGINARIUM_WINDOW` | `24` | Turns sent verbatim; older turns fold into a rolling summary. Set very high to disable summarising. |
| `IMAGINARIUM_SUMMARIZE_EVERY` | `12` | Slack before re-summarising. |
| `IMAGINARIUM_ACTION_RUN` | `1` | Consecutive lines one speaker may tag with an action. 0 disables. |
| `IMAGINARIUM_STALL_WINDOW` | `6` | Verbatim turns sent once the scene has locked. |
| `IMAGINARIUM_DEBUG` | unset | `1` prints the raw model output. |

Reasoning is turned off with `"think": false`. Builds that reject the
parameter fall back to a `/no_think` directive. `<think>` blocks are stripped
either way.

## Use

```bash
python cli.py char new    --world "Between the Stations"
python cli.py char new    --world "Between the Stations"   # sees the first
python cli.py loc new     --world "Between the Stations"
python cli.py session new --world "Between the Stations"   # blank premise = generated
python cli.py play 1
```

Also: `models`, `char list`, `char show <id>`, `loc list`, `rel list`,
`rel redo [--all] [--yes]`, `session list`.

`char new` is shown the existing cast and must produce someone who collides
with them, then writes the history and unresolved friction for each new pair.
A blank premise at `session new` generates a situation that worsens if nobody
speaks, plus an opening Narrator beat.

In `play` (`/?` lists everything):

```
vivienne <taps the desk> Then let's start.   speak manually
/ai marina                                   generate one line
/ai                                          next in rotation
/auto 8                                      eight alternating turns
/n The lights flicker.                       narrator beat
/add [name]   /drop <name>   /cast
/undo   /t   /temp 0.9   /model [tag]   /export   /q
```

Names prefix-match on first name, so `viv` works.

## Tests

```bash
python3 test_offline.py    # migration, relationships, prompts, summary, loop-breaker
python3 test_stream.py     # stream_line buffering
```

Both use a stub model. No Ollama, network or GPU.

Score a session for the failure modes below:

```bash
python3 scenestats.py [session_id ...]
```

## Design notes

**Persona goes last.** Order is system (world, cast, format), then transcript
(append-only), then the persona tail. Ollama reuses the KV cache for the
longest common prefix, so alternating speakers only re-prefill the tail.
Don't reorder.

**`bio` and `persona_prompt` are separate.** Bio is prose for the reader.
Persona is second-person instruction: what they want, what they hide, the
move they make when refused. Bios make bland personas.

**A good persona isn't enough.** Personas written in isolation, against an
absent human, produce interchangeable speakers. Characters are generated
against the cast, and every pair gets a `relationship` row: what each wants
from the other, what each won't say first, what would move each of them, and
the disagreement neither has resolved. A scene needs somewhere to go before
anyone speaks.

**No catchphrases.** `voice.avoids`, not `voice.tics`. A character given a
sentence-opening template locks onto it within two turns.

**Visual fields are written but unread.** `appearance`, `prompt_fragment`,
`renderer`, `style_tags`: one LLM call now saves a migration later.

**Strict markup from turn one.** `<action>` in angle brackets, `*bold*`,
`_italic_`. `clean_line()` converts asterisk actions to angle brackets and
strips a leading `Name:`. A leading `*emphasis*` is not an action and must
survive; a fabricated tag poisons the classifier corpus.

**Actions earn their place.** Only when the body does something the words
don't. `<looks away> I'm fine.` stays. `<smiles> That's funny.` goes. The rule
governs quality; `IMAGINARIUM_ACTION_RUN` caps frequency.

## What to watch for

- **Template lock.** Every line from a character opening the same way. Check
  the first two words of each speaker's lines. Two stuck generators are easy
  to tell apart and neither is a character. The anti-mirror resample fires on
  a third repeat; if it fires often, the format rules aren't working.
- **Shape lock.** Openings vary, but lines trade "X is Y" definitions, each
  picking up the last line's noun. Once detected, the speaker gets a nudge and
  the prompt window drops to `IMAGINARIUM_STALL_WINDOW`.
- **Action inflation.** Most lines carrying an action means the rule is being
  ignored.
- **Characters agreeing.** Friction comes from the relationship, not the
  persona. If scenes resolve smoothly, read `friction`: if one explanation
  would settle it, it's too weak.
- **Callbacks that stop landing.** The summary is lossy. If characters forget
  things from thirty turns back, raise `IMAGINARIUM_WINDOW`.

## Next

Stage Manager: constrained decode over the sprite inventory, classifying each
action as transient, postural, wardrobe, positional or affective. Build it
against exported transcripts before wiring any image generation.
