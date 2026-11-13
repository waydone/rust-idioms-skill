# rust-idioms skill

A local Claude Code skill for **Rust language-level idioms, design, and compiler-error diagnosis**. Hand-authored in the same lean, version-pinned, hub-and-spoke style as `salvo-skill`. Installed by symlinking this repo into `~/.claude/skills/rust-idioms-skill`.

## What it does

Helps write idiomatic, correct Rust and unblock compiler errors fast — focused on the *design reasoning* and *root-cause fix*, not syntax tutorials. High-increment content only: error-code → design-question mappings, type/ownership selection tables, the idiomatic choice among several that compile, and the gotchas (especially `async`/`Send` and lifetimes).

- Hub: `SKILL.md` (definition, version discipline, trigger boundary, reference index).
- `references/*.md` (11 topics, loaded on demand): ownership/borrowing/lifetimes, error-handling, traits/generics, iterators/closures, async-tokio, macros, testing, unsafe, modules/crates, compiler-errors (E0xxx table), anti-patterns.
- `evals/evals.json`: trigger-boundary + knowledge-correctness checks.

## Version assumptions

Pinned to **current stable Rust 1.100 (1.100.0) / edition 2024**. Every new `Cargo.toml` is `edition = "2024"` (the current edition and what `cargo new` writes — never the stale `2021`); existing projects match their `Cargo.toml`. Non-trivial third-party crate APIs (thiserror/anyhow/tokio/syn/quote/proptest) should be verified with Context7 before use.

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

The v0.1.2 round (2026-11-09) re-pins to **Rust 1.99.1**, driven by the official `RELEASES.md` for 1.96.0 – 1.99.1. Every new claim was checked on rustc/cargo 1.99.1 rather than taken from the notes: `assert_matches!`/`debug_assert_matches!` run (and are **not** in the prelude — `use std::assert_matches;`, which the notes don't say), `bool::ok_or`, `Option`/`Result::map_or_default`, `Vec::into_parts`/`from_parts`, `Box::into_non_null`/`from_non_null`, `std::i32::MAX` → `deprecated` warning, `raw_borrows_via_references` firing on `&mut x as *mut _`, `build.warnings = "deny"` failing the build, a workspace member's `default-features = false` really dropping an inherited default (`cargo tree -e features`), and the macro trailing-`;` diagnostic (deny-by-default locally — corrected a first draft that called it "silently dropped"). A generated harness (now in `tools/verify/`, see below; block counts are superseded by the 0.1.4 round) put all 55 `rust` blocks into one crate and ran `cargo check --all-targets` on 1.99.1: **41 compile with 0 errors and 0 warnings (no deprecated API)**; the 14 skipped are intentional fragments (free variables inside a body, ❌/✅ pairs defining the same fn twice, `mod x;` layout, macro-syntax illustration, method bodies without their `impl`) plus the proc-macro derive, which needs its own `proc-macro` crate — no API-level error appeared anywhere.

The v0.1.3 round (2026-11-13) re-pins to **Rust 1.100.0**, driven by the official `RELEASES.md` entry. The existing 41 compiled blocks stayed at **0 errors / 0 warnings** on 1.100.0, and `tests/new_on_stable.rs` grew four tests for the 1.100 claims: `Result<T, !>` with irrefutable `let Ok(..)`, `Infallible` being the same type as `!`, `bool::toggle`, `trim_prefix`/`trim_suffix`, `Vec::from_fn`, `Box::map`/`Rc::map`/`Arc::map`, `Box::take`, `mem::DropGuard` (+ `dismiss`), and `Vec::new_in`/`Box::new_in` with `System`/`Global`. Testing corrected a first draft: there is **no** blanket `From<!>`, so `?` on a `Result<_, !>` into a custom error is E0277 until the error type implements `From<!>`. Also reproduced outside the harness: E0119 for a trait implemented for both `!` and `Infallible`; E0308 passing `&Vec<u8, System>` to `&Vec<u8>`; `cargo::unused_dependencies` warning by default and failing the build at `deny`. The remaining 1.100 compatibility notes (closure capture in `if let`, `box` patterns, `min`/`max`, `cargo install` lockfile, publish-age, build-dir layout) are quoted from the release notes and marked as such in `SKILL.md`.

### Re-verifying (after editing the skill or on a new Rust release)

```sh
rustup update stable
tools/verify/check.sh
```

It regenerates `tools/verify/src/blocks/` from every ```` ```rust ```` block (one module each, `// file:line` header), runs `cargo check --all-targets` — which must report **0 errors and 0 warnings**, so a newly deprecated API fails the check — and runs `tests/new_on_stable.rs`, which pins the "new on stable" APIs `SKILL.md` recommends. Fragments that can't compile on their own are skipped by content in `tools/verify/harness.json` (`skip_if_contains`); application names snippets assume (`do_io`, `Config`, `User`, …) live in `tools/verify/src/common.rs`. When you add a "new on stable" bullet, add a test for it. A grep for the 1.96–1.99 breaking patterns (`std::i32::MAX`-style paths, `use S::{self as ..}`, `Type = Type` where-bounds, `pin!(&mut ..)`) found none in the skill.

The v0.1.4 round (2026-11-13) closes the gaps 0.1.3 left open. The 1.100 compatibility notes that were only quoted are now **reproduced by differential runs on 1.99.1 vs 1.100.0**: `box` patterns under `#[cfg(false)]` (warning → hard error), `Iterator::max`/`min` calling `Ord::max`/`Ord::min` and `max_by_key` changing its answer for an inconsistent `PartialOrd`, and `if let` with non-binding patterns no longer capturing (E0505 on 1.99.1, compiles on 1.100) — the last two are pinned as tests. Cargo's publish-age cooldown was exercised for real (`global-min-publish-age = "5 days"` resolved `salvo = "1.1"` to 1.1.1 instead of 1.1.2) and the `target/` layout change was diffed between the two toolchains. Still quoted only: the dyn-compatibility and `Box`-inference notes, and `cargo install` using the packaged lockfile. Harness: the proc-macro derive skeleton and its spanned-error guard are now compiled as a real `proc-macro` crate generated from `macros.md` (`gen_derive.py`) and used by `tests/derive.rs` on plain, generic + `where`, and enum types; fences nested in list items and `where` clauses at column 0 are handled, which un-skipped three more blocks — **46 of 55 blocks are compiled** (44 in the harness + 2 in the derive crate), 9 remain intentional fragments. `check_edition.py` now fails the run if any `[package]` example in the skill, or the harness's own manifests, is not `edition = "2024"`.

The v0.1.5 round (2026-11-13) reproduces the last quoted-only 1.100 notes on 1.99.1 vs 1.100.0: `cargo install --path` on a crate whose `Cargo.lock` pins `itoa 1.0.5` built 1.0.19 on 1.99.1 and 1.0.5 on 1.100; `<dyn T<A = u8> as T>::A` over a dyn-incompatible `T` compiled on 1.99.1 and is E0038 on 1.100. The `Box` allocator-generalization inference note could not be triggered with ~40 candidate expressions and is documented as such — it is the only 1.100 statement in the skill not backed by a local run.

## Changelog

- **0.1.5** (2026-11-13) — `cargo install` lockfile default and the dyn-incompatible projection error reproduced and described from observation; the `Box` inference note marked as the one unreproduced item.
- **0.1.4** (2026-11-13) — 1.100 compatibility notes reproduced against 1.99.1 and rewritten with what was observed (`if let` capture, `box` patterns, `max`/`min` → `Ord::max`/`Ord::min`); Cargo publish-age cooldown documented with the real config keys (`registry.global-min-publish-age`) and the new `target/` layout; tooling: proc-macro derive compiled for real (`hello-derive` + `tests/derive.rs`), 3 more blocks un-skipped (46/55 compiled), edition-2024 gate (`check_edition.py`) in `check.sh`.
- **0.1.3** (2026-11-13) — re-pinned to Rust **1.100**; never type (`Result<T, !>`, `Infallible` alias, the missing blanket `From<!>`, E0119) in `SKILL.md` + error-handling; stable allocator API (`Vec::new_in`, `Vec<T, A>` is a distinct type, `_in` raw-parts variants) in `SKILL.md` + unsafe; `trim_prefix`/`trim_suffix`, `Vec::from_fn`, `bool::toggle`, `Box`/`Rc`/`Arc::map`, `mem::DropGuard`; Cargo `[lints.cargo]` + default-on `unused_dependencies`, `cargo install` lockfile, publish-age, build-dir layout; edition-2024 rule widened to every `Cargo.toml` written and repeated in the description; 4 new `new_on_stable` tests.
- **0.1.2** (2026-11-09) — re-pinned to Rust **1.99**; "new on stable since 1.95" section in `SKILL.md`; `assert_matches!` (with the import gotcha) replaces `assert!(matches!(..))` in testing; `bool::ok_or` + `map_or_default` in error-handling; `Vec::into_parts`/`Box::into_non_null`, `&raw` vs `as *mut` + `raw_borrows_via_references`, union-pattern UB note in unsafe; workspace `default-features = false` override in modules-crates; outlined-module attribute macros + non-local trailing-semicolon lint in macros; Cargo `build.warnings` for CI; dropped the soybean-admin-salvo routing branch from the trigger description.
- **tooling** (2026-11-09, no plugin version bump — skill content unchanged) — verification harness committed as `tools/verify/` (`check.sh`, content-based fragment skips, `tests/new_on_stable.rs`); README re-verify instructions.
- **0.1.1** — compile-verified on 1.95.0; packaged as installable plugin.

## Install

- **As a plugin (marketplace):** `/plugin marketplace add waydone/rust-idioms-skill`, then `/plugin install rust-idioms@rust-idioms-skill`.
- **Local (symlink):** from the repo root, `ln -sfn "$PWD/skills/rust-idioms" ~/.claude/skills/rust-idioms`.

## Updating after an edit

Bump `version` in `.claude-plugin/{plugin,marketplace}.json`, `git push`, then `claude plugin marketplace update rust-idioms-skill && claude plugin update rust-idioms@rust-idioms-skill`, and restart. Without a version bump, `plugin update` reports "already at latest" and won't pull the new content.
