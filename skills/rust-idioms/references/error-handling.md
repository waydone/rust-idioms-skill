# Error handling

The core question is never "how do I write `?`" — it's **who is supposed to recover from this, and what do they need to know to do it?** That answer drives every choice below: `Result` vs `Option` vs `panic`, `thiserror` vs `anyhow`, how granular your enum is, and what crosses a public boundary.

Assumes Rust 1.99 / edition 2024. `thiserror` 2.x / `anyhow` 1.x API beyond textbook usage → verify with Context7 before writing.

## The decision tree: `Result` vs `Option` vs `panic`

| Situation | Use | Why |
|---|---|---|
| Failure the caller can plausibly handle / report | `Result<T, E>` | Carries *why* it failed; forces the caller to acknowledge it |
| Absence is an expected, ordinary outcome (key missing, end of input) | `Option<T>` | "Not there" is not an error; don't manufacture an error type for it |
| A bug — a contract the caller violated, an invariant *you* guarantee | `panic!` / `unreachable!` | Unrecoverable by definition; recovering would just hide the bug |
| Out of memory, stack overflow | abort (not yours to catch) | — |

The litmus test for `Result` vs `panic`: **could correct upstream code ever trigger this at runtime with untrusted input?** If yes → `Result`. If it can only happen when *your own code* is wrong → `panic`. A web request body that fails to parse is `Result` (the client sent garbage). An index that's out of bounds *after you just checked `len()`* is a `panic` candidate (your check is the contract).

`Option` is not a degenerate `Result`. Reaching for `Result<T, ()>` or `Result<T, NotFound>` where `Option<T>` would do loses you the `?`-with-context story and adds a type for no information. Conversely, returning `Option` for something that has *many* failure modes throws away the diagnosis — `parse()` returning `Option` would be useless; it returns `Result` so you learn *what* was wrong.

## `?` and `From`/`Into`: the conversion is the design

`?` desugars to roughly `match expr { Ok(v) => v, Err(e) => return Err(From::from(e)) }`. The `From::from` is the whole point: **`?` only compiles across error types if a `From<SourceError> for YourError` exists.** This is the mechanism, not a footnote.

So "make `?` work" is really "decide how the source error embeds into mine." Three idiomatic answers:

- **Wrap it as a variant** (libraries): `#[from]` on a `thiserror` variant generates the `From` impl. Each distinct source error gets a typed home.
- **Erase it** (applications): `anyhow::Error` has a blanket `From<E: std::error::Error + Send + Sync + 'static>`, so `?` swallows *any* such error into one type. You trade the type for ergonomics.
- **Map at the call site** when no `From` should exist: `.map_err(|e| MyError::Config(e.to_string()))?`. Use this when an automatic conversion would be *too* eager (e.g. you don't want every `io::Error` anywhere to silently become `MyError::Io`).

Anti-pattern: a giant `impl<E: Into<anyhow::Error>> From<E> for AppError`. It compiles, but it means **every** `?` lands in one opaque variant, so your match arms downstream can never distinguish cases. That's fine for a true catch-all *application* error; it's wrong for a *library* error whose callers need to branch.

## Libraries use `thiserror`; applications use `anyhow`

The dividing line is **whether your caller is your own `main`** (knows the whole system, just wants to print/log and exit) **or someone else's code** (needs to programmatically distinguish failures).

```toml
# Cargo.toml — a library crate
[package]
edition = "2024"

[dependencies]
thiserror = "2"   # verify exact 2.x attrs with Context7
```

```rust
// Library: a concrete, exhaustive, matchable error type.
use thiserror::Error;

#[derive(Debug, Error)]
pub enum StoreError {
    #[error("key {0:?} not found")]
    NotFound(String),

    #[error("config is invalid: {reason}")]
    BadConfig { reason: String },

    // #[from] generates `From<std::io::Error>`, so `?` works on io ops.
    #[error("disk I/O failed")]
    Io(#[from] std::io::Error),

    // `source` participates in the error chain without an auto-From
    // (use when you DON'T want every such error to convert implicitly).
    #[error("serialization failed")]
    Serde {
        #[source]
        source: serde_json::Error,
    },
}
```

```rust
// Application: don't make the caller match — they're going to log & bail.
use anyhow::{Context, Result, bail, ensure};

fn load_user(path: &str) -> Result<User> {
    let raw = std::fs::read_to_string(path)
        .with_context(|| format!("reading user file {path}"))?;     // adds context, keeps source
    let user: User = serde_json::from_str(&raw)
        .context("parsing user JSON")?;
    ensure!(user.id != 0, "user id must be non-zero");              // bug-or-bad-data → error, not panic
    Ok(user)
}
```

`thiserror` is a zero-cost compile-time derive — it only *generates* the `Display`/`Error`/`From` impls you'd otherwise hand-write; the resulting type is yours and has no runtime baggage. `anyhow::Error` is a single boxed, type-erased, backtrace-carrying error. Don't put `anyhow` in a library's public signatures — you'd force erasure on every downstream consumer.

## The boundary: mixing the two is correct, not a smell

A binary that depends on libraries naturally has both. The idiom:

- **Inside a library module**: return its own `thiserror` enum.
- **In application glue (`main`, request handlers, orchestration)**: use `anyhow::Result`, and let `?` absorb the library's typed error (because `StoreError: std::error::Error` ⇒ `anyhow`'s blanket `From` applies).
- **At the very edge where you must branch on a specific cause**, recover the type with `err.downcast_ref::<StoreError>()`.

```rust
fn run() -> anyhow::Result<()> {
    let user = load_user("u.json")?;        // anyhow::Error
    store.put(&user)?;                      // StoreError → absorbed into anyhow::Error
    Ok(())
}

fn main() -> anyhow::Result<()> {
    if let Err(e) = run() {
        // need to react to one specific library failure?
        if let Some(StoreError::NotFound(_)) = e.downcast_ref::<StoreError>() {
            eprintln!("nothing to do");
            return Ok(());
        }
        return Err(e);   // `main -> Result` prints Debug, i.e. the full {:?} chain + backtrace
    }
    Ok(())
}
```

Rule of thumb: **type-erase late, never early.** Keep the precise type as long as someone might branch on it; collapse to `anyhow::Error` only once the only remaining action is "report and stop."

## `.context()` — add *what you were doing*, keep *what failed*

A bare `io::Error: No such file or directory` is useless at the top of a call stack — *which* file, *during what*? `.context("...")` / `.with_context(|| ...)` (anyhow) prepend a layer while preserving the underlying error as `source`, so the chain reads top-down: `parsing user JSON: No such file (config.toml)`. Use the closure form when building the message allocates, so it's only paid on the error path.

`thiserror`'s equivalent is structural, not a method: encode the context *in the variant* (`BadConfig { reason }`, the `#[error("…{path}…")]` format string) so callers get it typed rather than as a string.

Anti-pattern — **context that drops the source**: `.map_err(|_| MyError::Failed)?` throws away the original cause. Now nobody can debug it. Either carry the source (`#[from]`/`#[source]`) or at minimum interpolate it (`format!("...: {e}")`).

## `unwrap` / `expect` / `unreachable!` — when a panic is the *right* answer

These are legitimate when the failure genuinely **cannot happen unless the program is buggy** — panicking is then strictly better than threading an impossible error type everywhere.

| Construct | Use when | Note |
|---|---|---|
| `expect("msg")` | Invariant you can name; const/startup config that must be valid | Prefer over `unwrap()` — the message is the panic's documentation. Treat `expect` as "this holds *because* …" |
| `unwrap()` | Quick prototypes, tests, `examples/` | In tests it's fine and idiomatic; in prod prefer `expect` so the failure self-documents |
| `unreachable!()` | A match arm the type system can't prove is dead but you've reasoned is | If it's actually reachable on bad input, it's a `Result`, not this |
| `todo!()` / `unimplemented!()` | Stubs during development | Never ship |
| `panic!("…")` | Detected a broken invariant mid-function | Same bar as `expect` |
| `debug_assert!` | Expensive invariant check you want only in debug builds | Compiled out in release |

In `main` / tests / build scripts, `unwrap`/`expect` are *idiomatic* — the program's whole job is to crash loudly on bad config. The anti-pattern is `unwrap()` in **library code or a long-running service request path**, where one bad input takes down the process (or panics across an FFI/`#[handler]` boundary). There, convert to `Result`.

`let Some(x) = opt else { return Err(...) };` (let-else) is the idiomatic way to bail on a `None` you *can* recover from without nesting — reach for it before `match`/`if let` pyramids.

For a **boolean** precondition, 1.98's `bool::ok_or` / `ok_or_else` turns it into a `Result` you can `?`: `(!name.is_empty()).ok_or(ConfigError::Empty)?;` (`ok_or_else(|| ..)` when building the error is costly). And `Option::map_or_default` / `Result::map_or_default` (1.98) replace `map_or(Default::default(), f)` / `.map(f).unwrap_or_default()` — `let len = opt.map_or_default(|s| s.len());`.

**Infallible results (1.100).** The never type is stable: an operation that cannot fail returns `Result<T, !>`, and the caller writes `let Ok(v) = infallible();` — the pattern is irrefutable because `Err(!)` cannot exist. `std::convert::Infallible` is now just an alias of `!`, so the `Err` type of `String: FromStr` or of a blanket `TryFrom` is `!` too. `?` does **not** make a never error disappear into your error type by itself: add `impl From<!> for MyError { fn from(never: !) -> Self { never } }` once (the body type-checks because `!` coerces to anything), or destructure with `let Ok(..)` instead of `?`.

## Custom `Error` design — granularity

The variant count is a UX decision for your caller, not a style preference:

- **One variant per failure mode the caller can act on differently.** If two failures lead to identical caller behavior, merging them is fine — don't split for decoration.
- **Don't leak implementation crates into your public error.** A `#[from] sqlx::Error` in your public API ties every consumer to your current DB crate. Wrap it behind a neutral variant (`Storage(String)` or your own `StorageError`) if you might swap backends.
- **`#[non_exhaustive]`** on a public error enum lets you add variants later without a breaking change; the cost is callers must add a `_ =>` arm. Worth it for libraries with an evolving error surface.
- **Implement `std::error::Error` (which `thiserror` does for you), not just `Display`.** The `source()` chain is what tools/`anyhow` walk; skipping it breaks chaining.
- Don't reach for `Box<dyn std::error::Error>` in a library's signatures as a "lazy `anyhow`": it's `!Send`/`!Sync` by default (so it won't cross threads/`.await` cleanly) and gives callers nothing to match on. If you want erasure, `anyhow` is the better-engineered version; if you want types, write the enum.

## Bridging to a web handler return type (language layer)

A web handler is just a function whose error type must turn into a response. At the **language level** the pattern is: your handler returns `Result<Success, AppError>`, and `AppError` implements whatever trait the framework uses to render a response — built on the same `From`/`?` machinery as everywhere else.

```rust
// Pure-language sketch: `IntoResponse` here stands in for the framework's
// response trait. (Salvo's actual trait/API → use salvo-skill.)
pub enum ApiError {
    NotFound,
    Unauthorized,
    Internal(anyhow::Error),   // catch-all for "something broke" — log, don't leak
}

impl ApiError {
    // Explicit catch-all conversion for internal failures; typed variants stay explicit.
    fn internal<E: Into<anyhow::Error>>(e: E) -> Self {
        ApiError::Internal(e.into())
    }

    fn parts(&self) -> (u16, &'static str) {
        match self {
            ApiError::NotFound     => (404, "not found"),
            ApiError::Unauthorized => (401, "unauthorized"),
            ApiError::Internal(_)  => (500, "internal error"),  // generic public message
        }
    }
}
```

Two language-level disciplines that matter here:

1. **The error→status mapping lives in one place** (the response-conversion impl), so handlers stay `Ok(data)? / typed-error?` and never hand-build status codes. Keep typed cases explicit, and route opaque internal failures through `ApiError::internal(e)` / `.map_err(ApiError::internal)?`; don't add a blanket `From<E>` unless you've deliberately accepted that every unmatched error becomes the same 500.
2. **Public message vs. logged detail are different audiences.** Log the rich `source()` chain (`tracing::error!(error = ?e)`); return a generic string to the client. Putting `e.to_string()` in a 500 body leaks internals (paths, SQL, secrets). Map detail in, render detail out — never the same string for both.

For the concrete framework trait, response negotiation, catchers, and panic-to-500 recovery, that's **framework API** → defer to salvo-skill.
