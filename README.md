# rust-idioms skill

A local Claude Code skill for **Rust language-level idioms, design, and compiler-error diagnosis**. Hand-authored in the same lean, version-pinned, hub-and-spoke style as `salvo-skill`. Installed by symlinking this repo into `~/.claude/skills/rust-idioms-skill`.

## What it does

Helps write idiomatic, correct Rust and unblock compiler errors fast — focused on the *design reasoning* and *root-cause fix*, not syntax tutorials. High-increment content only: error-code → design-question mappings, type/ownership selection tables, the idiomatic choice among several that compile, and the gotchas (especially `async`/`Send` and lifetimes).

- Hub: `SKILL.md` (definition, version discipline, trigger boundary, reference index).
- `references/*.md` (11 topics, loaded on demand): ownership/borrowing/lifetimes, error-handling, traits/generics, iterators/closures, async-tokio, macros, testing, unsafe, modules/crates, compiler-errors (E0xxx table), anti-patterns.
- `evals/evals.json`: trigger-boundary + knowledge-correctness checks.

## Version assumptions

Pinned to **current stable Rust 1.99 (1.99.1) / edition 2024**. New projects default to `edition = "2024"` (never the stale `2021`); existing projects match their `Cargo.toml`. Non-trivial third-party crate APIs (thiserror/anyhow/tokio/syn/quote/proptest) should be verified with Context7 before use.

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

The v0.1.2 round (2026-11-09) re-pins to **Rust 1.99.1**, driven by the official `RELEASES.md` for 1.96.0 – 1.99.1. Every new claim was checked on rustc/cargo 1.99.1 rather than taken from the notes: `assert_matches!`/`debug_assert_matches!` run (and are **not** in the prelude — `use std::assert_matches;`, which the notes don't say), `bool::ok_or`, `Option`/`Result::map_or_default`, `Vec::into_parts`/`from_parts`, `Box::into_non_null`/`from_non_null`, `std::i32::MAX` → `deprecated` warning, `raw_borrows_via_references` firing on `&mut x as *mut _`, `build.warnings = "deny"` failing the build, a workspace member's `default-features = false` really dropping an inherited default (`cargo tree -e features`), and the macro trailing-`;` diagnostic (deny-by-default locally — corrected a first draft that called it "silently dropped"). A generated harness then put all 52 `rust` blocks into one crate and ran `cargo check --all-targets` on 1.99.1: **39 compile with 0 errors and no deprecation warnings**; the other 12 are intentional fragments (free variables inside a body, ❌/✅ pairs, `mod x;` layout, macro-syntax illustration) and the proc-macro derive needs its own `proc-macro` crate — no API-level error appeared anywhere. A grep for the 1.96–1.99 breaking patterns (`std::i32::MAX`-style paths, `use S::{self as ..}`, `Type = Type` where-bounds, `pin!(&mut ..)`) found none in the skill.

## Changelog

- **0.1.2** (2026-11-09) — re-pinned to Rust **1.99**; "new on stable since 1.95" section in `SKILL.md`; `assert_matches!` (with the import gotcha) replaces `assert!(matches!(..))` in testing; `bool::ok_or` + `map_or_default` in error-handling; `Vec::into_parts`/`Box::into_non_null`, `&raw` vs `as *mut` + `raw_borrows_via_references`, union-pattern UB note in unsafe; workspace `default-features = false` override in modules-crates; outlined-module attribute macros + non-local trailing-semicolon lint in macros; Cargo `build.warnings` for CI; dropped the soybean-admin-salvo routing branch from the trigger description.
- **0.1.1** — compile-verified on 1.95.0; packaged as installable plugin.

## Install

- **As a plugin (marketplace):** `/plugin marketplace add waydone/rust-idioms-skill`, then `/plugin install rust-idioms@rust-idioms-skill`.
- **Local (symlink):** from the repo root, `ln -sfn "$PWD/skills/rust-idioms" ~/.claude/skills/rust-idioms`.

## Updating after an edit

Bump `version` in `.claude-plugin/{plugin,marketplace}.json`, `git push`, then `claude plugin marketplace update rust-idioms-skill && claude plugin update rust-idioms@rust-idioms-skill`, and restart. Without a version bump, `plugin update` reports "already at latest" and won't pull the new content.
