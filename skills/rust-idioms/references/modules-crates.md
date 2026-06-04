# Modules, crates & workspaces

The module system is about **API surface** and **compile boundaries**, not folders. Two distinct decisions: (1) *what is visible to whom* (`pub`/`pub(crate)`/`pub(super)`), and (2) *what is a separate compilation unit* (a crate). Get these wrong and you either leak internals into your semver-stable surface or pay recompile cost you didn't need to.

## Visibility: pick the narrowest that works

Default to private. Widen only when a real caller needs it. `pub` on an item inside a `pub mod` is part of your crate's **public API** — it's a semver commitment.

| Modifier | Visible to | Use for |
|---|---|---|
| (none) | this module + descendants | the default; most items |
| `pub(self)` | same as none (explicit) | rarely; documenting intent |
| `pub(super)` | parent module | a helper the parent orchestrates but outsiders shouldn't touch |
| `pub(crate)` | anywhere in this crate | cross-module internals — the workhorse for non-API sharing |
| `pub(in path)` | a named ancestor module | a sub-tree-internal item; niche |
| `pub` | external crates (if path is reachable) | the actual public API — a semver promise |

Key trap: `pub` does **not** mean "exported". An item is externally reachable only if **every module on its path is also `pub`**. A `pub fn` inside a private `mod` is crate-internal in practice. Use this deliberately: keep `mod` private and selectively `pub use` items up to the root (see re-exports).

```rust
// fragment
mod engine {          // private module
    pub fn run() {}   // pub, but engine isn't pub → NOT in public API
    pub(crate) fn tune() {}
}
pub use engine::run;  // THIS is what makes `run` public, at crate root
```

`pub(crate)` is the right default for "shared across my modules but not my contract". Reaching for plain `pub` to silence a privacy error is the #1 way internals leak into your stable surface.

## Path rules (edition 2018+, unchanged through 2024)

- Paths in `use` are **absolute from a crate root by default**: `crate::`, a dependency name, or `std`/`core`/`alloc`. No leading `::`/`self::` boilerplate needed.
- `self::` = current module, `super::` = parent, `crate::` = this crate's root.
- `extern crate` is **obsolete** — dependencies in `Cargo.toml` are usable by name directly. The only surviving niche is aliasing or linking sysroot crates like `alloc` in `no_std`.
- `mod foo;` loads `foo.rs` **or** `foo/mod.rs` (prefer `foo.rs` + `foo/` for submodules; the bare `mod.rs` style still works but the file-named style is idiomatic since 2018).

There is no separate edition-2024 path change to memorize here — the 2018 module overhaul is still the model. Don't emit `extern crate` in new code; that's a stale-training-data tell.

## bin vs lib: split early

A common shape is **a `lib` crate holding all logic + a thin `bin` that calls it**. Reasons: integration tests (`tests/`) and doc tests can only exercise a **library**, not a binary's internals; and downstream reuse needs a lib. A package can have one `lib` and many `bin`s.

```
my_app/
  Cargo.toml
  src/
    lib.rs        # all real logic, the testable surface
    main.rs       # `fn main()` → calls my_app::run()
    bin/tool.rs   # extra binary: `cargo run --bin tool`
  tests/
    api.rs        # integration test: `use my_app::...` — sees only `pub` items
```

`main.rs` and `lib.rs` in the same package form **two crates** sharing the package name. `main.rs` refers to the lib by the package name (`use my_app::run;`), not `crate::`. If you only have `main.rs`, your integration tests can't reach internal functions — that's the usual reason to extract a lib.

## Workspaces: when to split into multiple crates

Split a crate out when there's a **real boundary**: independent versioning/publish, a genuine compile-time win (a leaf crate that rarely changes stops invalidating everything above it), or enforcing a dependency direction. Do **not** split just for tidy folders — modules already give you that for free, and extra crates add `Cargo.toml` overhead and slower clean builds.

```toml
# workspace root Cargo.toml — edition 2024
[workspace]
resolver = "3"               # the edition-2024 default resolver; set it explicitly at the root
members = ["crates/*"]

[workspace.package]          # inherited fields
edition = "2024"
version = "0.1.0"

[workspace.dependencies]     # single source of truth for versions
serde = { version = "1", features = ["derive"] }
```

```toml
# crates/core/Cargo.toml
[package]
name = "core"
edition.workspace = true     # inherit
version.workspace = true

[dependencies]
serde.workspace = true       # inherit version + features from the root
```

Notes that bite: `resolver = "3"` is the edition-2024 default but is **only honored at the workspace root** — a per-crate `edition = "2024"` does not flip the workspace resolver. Workspace members share one `Cargo.lock` and one `target/`. Cyclic crate dependencies are forbidden (modules may be mutually recursive; crates may not). Verify exact `[workspace.dependencies]` inheritance syntax against current Cargo docs (Context7) if you hit an error — the `.workspace = true` form is stable but easy to mistype.

## Feature flags & `cfg`: additive only

Cargo features are **purely additive** — enabling a feature must never *remove* items or *change* behavior of existing ones, because features unify across the dependency graph (if any crate turns on `foo`, everyone gets it). Designing a feature that's mutually exclusive with another, or that swaps an impl, breaks when two dependents disagree.

```toml
# Cargo.toml — edition 2024
[features]
default = ["std"]
std = []
serde = ["dep:serde"]        # `dep:` keeps the optional dep from auto-becoming a feature name
json = ["serde"]             # features enabling features

[dependencies]
serde = { version = "1", optional = true }
```

```rust
// fragment
#[cfg(feature = "serde")]
impl serde::Serialize for Config { /* ... */ }

#[cfg(not(feature = "std"))]
extern crate alloc;          // legit `extern crate` survivor: no_std

#[cfg(target_os = "linux")]
fn platform_init() { /* ... */ }
```

Idiom: gate the *addition* of impls/modules, never the *subtraction*. Use `dep:serde` (not bare `serde`) in the feature list so the optional dependency doesn't silently leak in as an implicit feature name. For test-only items use `#[cfg(test)]`; for "only when compiled as a dependency of tests elsewhere" use a `dev-dependencies` + feature combo.

## Re-exports (`pub use`) shape the public API

`pub use` is how you **decouple internal layout from external surface**. Organize source into many small private `mod`s for your own sanity, then curate a flat, stable public API at the crate root with `pub use`. Moving a type between internal modules then doesn't break downstream code.

```rust
// fragment — src/lib.rs
mod client;
mod error;
mod transport;

pub use client::Client;          // flat surface
pub use error::{Error, Result};
// transport stays fully private — an implementation detail
```

This is also the fix for the "I want the type public but its module ugly/internal" tension: keep the module private (`mod`, not `pub mod`) and `pub use` the chosen items. Use `pub(crate) use` to make a convenient internal alias without exporting it. Be aware that a glob `pub use foo::*` re-exports *future* additions too — fine for a prelude you control, risky across a dependency boundary.

## Diagnosing E0432 / E0433

Both are "can't resolve a path"; the split is *where* resolution failed.

| Error | Means | First thing to check |
|---|---|---|
| **E0433** | failed to resolve — a path segment (often the **crate/root**) isn't found | typo'd crate name; dep missing from `Cargo.toml`; missing `mod foo;`; wrong `crate::`/`super::` anchor; a feature gating the item is off |
| **E0432** | unresolved **`use`** import — the `use` line itself is bad | the item exists but isn't `pub`/`pub(crate)` enough; wrong path after a refactor; importing a `bin`-only item from a test; feature-gated re-export disabled |

Root-cause checklist, in order:

1. **Is the dep actually declared?** `cargo tree -i <crate>` confirms it's in the graph for this build. A missing or feature-disabled dependency is the most common E0433.
2. **Is a feature off?** If the item lives behind `#[cfg(feature = "x")]`, the path resolves to nothing until you enable `x`. The error gives no hint about features — check the source/docs.
3. **Did you forget `mod foo;`?** A file in `src/` is invisible until some ancestor declares the module. Creating `src/foo.rs` alone does nothing.
4. **Is every module on the path public enough?** A `pub fn` behind a private `mod` is E0432 from outside — `pub use` it or widen the module.
5. **Right anchor?** `crate::` vs `super::` vs a bare dependency name. After moving code, `super::` often now points one level wrong.
6. **bin/lib confusion:** integration tests and other crates can only see the **lib's `pub`** items; functions only in `main.rs` are unreachable from `tests/` — extract them into the lib.

Don't fix E0432 by reflexively slapping `pub` on everything until it compiles — that grows your semver surface silently. Widen to the **narrowest** modifier that resolves the path (usually `pub(crate)`), and reserve `pub` for genuine API.

## Anti-patterns

- **`pub` everywhere to dodge privacy errors** — silently commits internals to your stable API. Use `pub(crate)`/`pub use` instead.
- **`extern crate` in new code** — obsolete since 2018 except `no_std`'s `alloc`.
- **Splitting crates for cosmetics** — modules give structure for free; extra crates cost build time and ceremony. Split for versioning/compile-isolation/dependency-direction, not folders.
- **Mutually-exclusive or behavior-changing features** — breaks under feature unification. Features must be additive.
- **`mod.rs` everywhere (old style)** — prefer `foo.rs` + `foo/` for the file-named layout idiomatic since 2018.
- **Per-crate `edition` expecting it to set the resolver** — `resolver = "3"` lives at the workspace root only.
