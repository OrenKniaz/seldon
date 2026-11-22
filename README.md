# Seldon

**Seldon turns a person's dream into a working product, using AI coding agents to plan, build and prove it.**

You describe what you want in plain words. Seldon grows the idea with you, crystallizes it into
software specs written for agentic development, hands each milestone to a coding agent (Codex or
Claude Code), and then checks the result itself, by walking your path through the real product,
before it calls anything done. It remembers every promise across sessions, so later milestones
can't silently break earlier ones.

## Why

A single agent chat is good at writing code and bad at keeping promises over a long project: it
forgets constraints, drifts from what was agreed, and reports "done" without having seen the thing
work. Seldon is the layer around the agent that handles the parts a good tech lead would:

- **Grow and align.** It asks the questions that matter (with options plus "Other"), then locks the
  agreed vision.
- **Spec before code.** Each milestone is a plain-words spec: what the person can do, how we'll see
  it, and what would mean it isn't met. Coding starts only after the person approves it.
- **One strong builder.** The builder owns execution, including any parallel sub-agents.
- **Independent proof.** Seldon runs the product, walks the user path, and judges what the person
  would experience, including human-style UI/UX feedback. Earlier promises are re-proved at every
  milestone.
- **Memory.** Decisions, promises and evidence live in a local SQLite store, not in a chat window.

Design principle: **code owns structure, the LLM owns meaning.** Every decision Seldon makes is one
schema-bound LLM call. Code handles state, identity and execution, and never pretends to understand
semantics.

## Two shapes

| Shape | What it is | Status |
|---|---|---|
| **Lead** (planner only) | One builder per milestone. Seldon plans, remembers and proves. | Current focus |
| **Orchestrator** (planner + workers) | Splits a dream along real seams and runs builders in parallel waves under just-in-time contracts. | Paused until Lead is solid |

## Status

Research-grade and moving fast. Seldon is measured against plain Codex on a ladder of real
"dreams" (from a hello-world CLI to small full-stack apps), with blind oracle scoring and real
provider runs as the primary signal. The current work is making Lead's cost and wall time come
close to a plain agent while keeping its proof and memory advantages.

## Try it

Requires Python 3.11+ and the Codex CLI or Claude Code.

```sh
pip install .
codex mcp add seldon -- seldon mcp     # or: claude mcp add seldon -- seldon mcp
```

Then tell the agent your dream.

## Tests

Tests are behaviour-only journeys over the golden path (no unit tests, no timing asserts):

```sh
bash scripts/gate.sh local            # writes temp output under C:/seldon/tests/local-1 (set GATE_ROOT to change)
```

## Design documents

- [CHARTER.md](CHARTER.md): the binding goal and core pieces
- [CHARTER-LEAD.md](CHARTER-LEAD.md) and [BLUEPRINT-LEAD.md](BLUEPRINT-LEAD.md): the Lead shape
- [BLUEPRINT.md](BLUEPRINT.md): the Orchestrator shape for large dreams

## License

[MIT](LICENSE) © 2026 Oren Kniaz
