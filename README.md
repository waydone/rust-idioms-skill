# rust-idioms skill

A local Claude Code skill for **Rust language-level idioms, design, and compiler-error diagnosis**. Hand-authored in the same lean, version-pinned, hub-and-spoke style as `salvo-skill`. Installed by symlinking this repo into `~/.claude/skills/rust-idioms-skill`.

## What it does

Helps write idiomatic, correct Rust and unblock compiler errors fast — focused on the *design reasoning* and *root-cause fix*, not syntax tutorials. High-increment content only: error-code → design-question mappings, type/ownership selection tables, the idiomatic choice among several that compile, and the gotchas (especially `async`/`Send` and lifetimes).

- Hub: `SKILL.md` (definition, version discipline, trigger boundary, reference index).
- `references/*.md` (11 topics, loaded on demand): ownership/borrowing/lifetimes, error-handling, traits/generics, iterators/closures, async-tokio, macros, testing, unsafe, modules/crates, compiler-errors (E0xxx table), anti-patterns.
- `evals/evals.json`: trigger-boundary + knowledge-correctness checks.

## Version assumptions

Pinned to **current stable Rust 1.95 / edition 2024**. New projects default to `edition = "2024"` (never the stale `2021`); existing projects match their `Cargo.toml`. Non-trivial third-party crate APIs (thiserror/anyhow/tokio/syn/quote/proptest) should be verified with Context7 before use.

## Division of labor (don't overlap)

| Layer | Owner |
|---|---|
| Rust **language** (ownership, traits, async/Send, macros, testing, unsafe, idioms, E0xxx) | **rust-idioms** (this skill) |
| **Salvo framework** API (routing, handlers, JwtAuth, OpenAPI, middleware) | `salvo-skill` |
| **Tooling** (go-to-def, find-refs, types, navigation) | `rust-analyzer-lsp` (LSP, already installed) |

A cross-layer question is split: e.g. "my Salvo handler's future isn't Send" → rust-idioms owns the `Send` reasoning, salvo-skill owns the handler signature.

## Properties

Zero hooks, zero MCP servers, zero background agents, no extra permissions — pure reference text loaded on demand.

## Verification

All claimed-compilable code snippets across the references were assembled and `cargo check`-verified on rustc/cargo 1.95.0 (edition 2024), including a real proc-macro crate (derive end-to-end), async/Send cases, and the test snippets (unit + doctests). Built/maintained 2026-06 via the skill-creator flow with codex/agy compile-verification.
