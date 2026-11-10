---
name: rust-idioms
description: "Rust language-level idioms, design, and compiler-error diagnosis (assumes current stable Rust 1.99 / edition 2024, new projects use edition 2024 not the stale 2021). USE for ANY non-framework Rust question: ownership/borrowing/lifetimes (E0382/E0499/E0502/E0505/E0506/E0507/E0515/E0596/E0597/E0716), error handling (Result/Option/panic, ?, thiserror vs anyhow), traits/generics/dyn & object safety (E0038/E0277), iterators/closures (Fn/FnMut/FnOnce), async/tokio (Send across .await, spawn vs spawn_blocking, !Send guards), macros (macro_rules!/proc-macros), testing (unit/integration/doc/proptest/tokio::test), unsafe & FFI, module/crate/workspace layout, and 'why won't this compile' / 'what's the idiomatic way' questions. Casual mentions count ('borrow checker won't let me', 'is this idiomatic'). SKIP and defer when: (a) the question targets a specific web-framework API — Salvo (Writer/handler/router/JwtAuth/…) goes to salvo-skill, axum/actix/rocket/warp/poem/hyper/tower stay with that framework (not mine); (b) pure code-navigation/jump-to-def (rust-analyzer-lsp); (c) it's NOT really a language question — toolchain/linker/build-system failures (undefined symbol, missing system lib, cargo/cc/ld link errors, -lfoo, build.rs linking, pkg-config), or pure frontend/CSS/other-language/other-stack work. A linker error from cargo build is a build/system issue, not a Rust language idiom. Zero hooks / zero MCP."
---

# Rust idioms, design & compiler-error diagnosis

Help the user write **idiomatic, correct, well-designed Rust** — and unblock them fast when the compiler says no. The user is AI-first and competent: give the *design reasoning* and the *root-cause fix*, not a syntax tutorial. Most of the value here is in: error-code → design-question mappings, type/ownership selection tables, the idiomatic choice among several that compile, and the gotchas that bite (especially around `async`/`Send` and lifetimes).

This skill is the **language layer**. It complements two things you likely already have:
- **salvo-skill** — the *framework* layer (Salvo API, routing, handlers, JwtAuth, OpenAPI). If the question is "how do I do X in Salvo", defer to that skill.
- **rust-analyzer-lsp** — the *tooling* layer (go-to-def, find-refs, types). If the user just needs navigation/lookup, the LSP answers directly; don't reach for this skill for that.

## Version discipline (read first)

This skill assumes **current stable Rust = 1.99** (rustc/cargo 1.99.1, 2026-10) and **edition 2024** (stabilized in Rust 1.85, Feb 2025).

**New on stable since 1.95 — reach for these instead of the older workaround** (each checked by compiling on 1.99.1):
- **1.96 `assert_matches!` / `debug_assert_matches!`** — replaces `assert!(matches!(..))` and prints the value on failure. ⚠️ Not in the prelude: `use std::assert_matches;` (and `use std::debug_assert_matches;`). Guards work: `assert_matches!(r, Err(E::Bad(s)) if s == "x")`.
- **1.98 `bool::ok_or(err)` / `ok_or_else(f)`** — `(!s.is_empty()).ok_or(Error::Empty)?;` instead of an `if … { return Err(..) }`. **`Option::map_or_default` / `Result::map_or_default`** — `opt.map_or_default(|s| s.len())` instead of `map_or(0, …)` / `map(..).unwrap_or_default()`.
- **1.99 `Vec::into_parts` / `Vec::from_parts`** and **`Box::into_non_null` / `Box::from_non_null`** — `NonNull`-based raw-parts round trips; prefer them to the `*mut`-based `into_raw_parts`/`from_raw_parts` dance in unsafe code.
- **1.99 legacy integer modules are fully deprecated** — write `i32::MAX`, never `std::i32::MAX` (now a `deprecated` warning). 1.97 deprecated the `std::char` constants/functions likewise (use `char::MAX`, `char::from_u32`).
- **Tooling:** Cargo `build.warnings = "deny"` (1.97, `.cargo/config.toml` `[build]`) replaces `RUSTFLAGS=-Dwarnings` in CI without invalidating the build cache; `cargo -m` = `--manifest-path`; edition-2024 workspace members may set `default-features = false` on an inherited dependency (1.99). Behavior changes that can surface in old code: some ambiguous glob imports became hard errors (1.98); `pin!(x)` no longer deref-coerces a `&mut T` (1.97); a hand-written `PartialOrd` that disagrees with a derived `Ord` now misbehaves (1.98 fast path).

- **For a NEW project, use `edition = "2024"`.** Do **not** reflexively emit `edition = "2021"` — that's a stale training-data default, and it silently opts out of edition-2024 behavior (e.g. the new `gen` keyword, RPIT lifetime capture rules, `unsafe extern`, disjoint closure captures already from 2021, `if let` temporary scope changes). When you scaffold a `Cargo.toml`, default to 2024.
- **For an EXISTING project, match what its `Cargo.toml` already declares** — don't "upgrade" the edition as a side effect of an unrelated change; an edition bump is its own deliberate task (`cargo fix --edition`).
- State the rust/edition you assumed when it matters, the way salvo-skill pins its target version. If a feature depends on a recent rustc, say so.

## Verify non-trivial third-party crate APIs with Context7

Your training data lags the current releases of `thiserror`, `anyhow`, `tokio`, `syn`/`quote`, `proptest`, `criterion`, `serde`, etc. For anything beyond textbook usage of these crates, query Context7 before writing the code (e.g. `tokio` 1.x `JoinSet`/`select!` semantics, `thiserror` 2.x attributes, `anyhow` 1.x context/downcast APIs, `syn` 2.x AST). Standard-library and language constructs (ownership, traits, `?`, iterators) you can write directly. When unsure, say the API is unverified and recommend `cargo check`.

## Standing rules

- **Root-cause over patch.** When code won't compile, explain *why* the design hit the wall and give the fix that addresses it — not a reflexive `.clone()`, `'static`, `Box::leak`, or `unsafe` that silences the checker while hiding the real issue. If a clone *is* the right call, say why.
- **Idiomatic, not just compiling.** Prefer the standard-library / community-idiomatic form. Note when a terser form exists.
- **Verify what you can.** Treat conclusions as needing `cargo check` (or `cargo test` / `cargo expand` / `miri` for macros/unsafe). Mark anything you couldn't run.
- This skill ships **no hooks, no MCP server, no background agents, and requests no extra permissions** — it is pure reference text loaded on demand.

## How to use the references

Load only the file you need (hub-and-spoke — keep context lean):

| Read | When the question is about |
|---|---|
| `references/ownership-borrowing-lifetimes.md` | move vs `&`/`&mut`, `Rc`/`Arc`/`Cow`/`Box` choice, lifetimes, and the borrow/lifetime error codes (E0382/E0499/E0502/E0505/E0506/E0507/E0515/E0596/E0597/E0716) |
| `references/error-handling.md` | `Result`/`Option`/`panic` decision, `?` + `From`, `thiserror` (libs) vs `anyhow` (apps), custom error design, `.context()` |
| `references/traits-generics.md` | generics (static dispatch) vs `dyn Trait` (dynamic dispatch), object safety (E0038), bounds/`where`, associated types, blanket impls + orphan rule, `impl Trait`, `Send + Sync + 'static` bounds (E0277) |
| `references/iterators-closures.md` | iterator adapters, `collect` target types, `Fn`/`FnMut`/`FnOnce` + `move` capture, returning `impl Iterator`, avoiding needless `collect`/`clone` |
| `references/async-tokio.md` | `async`/`.await`, futures `Send` ("future is not Send"), holding `!Send` (`MutexGuard`, `Rc`) across `.await`, `tokio::spawn` vs `spawn_blocking` vs `rayon`, `tokio::sync` vs `std::sync`, cancellation/timeouts/`select!`, and Salvo-handler `Send + Sync + 'static` / shared-state-via-`Arc` patterns |
| `references/macros.md` | `macro_rules!` (fragment specifiers, repetition, hygiene, `#[macro_export]`), proc-macros (derive/attribute/function-like, `syn`/`quote`), `cargo expand`, macro-vs-function/generics |
| `references/testing.md` | `#[test]`/`#[cfg(test)]`, unit vs integration (`tests/`), doc tests, `Result`-returning tests, `proptest`/`criterion`/`insta`/`mockall`, `tokio::test` async tests, `cargo test`/`nextest`, and testing Salvo handlers with `TestClient` |
| `references/unsafe.md` | when `unsafe` is legitimate, `SAFETY`/`/// # Safety`, `MaybeUninit`, `NonNull`/pointer arithmetic, `static mut` → `Atomic`/`Mutex`, `transmute`, FFI (`extern "C"`, `#[repr(C)]`, `CString`/`CStr`, no-panic-across-FFI), `miri` |
| `references/modules-crates.md` | `mod`/`use`/visibility (`pub(crate)`/`pub(super)`), bin vs lib, workspaces, feature flags + `cfg`, re-exports as public API, E0432/E0433 |
| `references/compiler-errors.md` | a fast `E0xxx` lookup table — each with root cause, minimal fix, and a "should you redesign?" prompt |
| `references/anti-patterns.md` | `.clone()`/`'static`/`Box::leak`/`unwrap`-in-prod/silent-error-swallow/`Box<dyn Error>`/blocking-in-async/`Arc<Mutex>`-everywhere — each with the better alternative |

If a question spans layers (e.g. "my Salvo handler won't compile because the future isn't Send"), this skill owns the *Rust* half (the `Send` reasoning) and salvo-skill owns the *Salvo* half (handler signature).
