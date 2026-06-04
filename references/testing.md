# Testing

Assumes Rust 1.95 / edition 2024. Test code is just Rust, so the language idioms in the other references apply. What's *specific* to testing is **where** a test lives (and what that implies about visibility), **how** you express the assertion/failure, and **which** third-party tool you reach for. This file is the decision layer, not a `cargo test` man page.

## Where a test lives — and why it matters

| Location | `#[cfg(test)]`? | Sees private items? | Use for |
|---|---|---|---|
| `mod tests` inside the source file | yes (gate the module) | **yes** — same crate, child module | unit tests of private fns / internal invariants |
| `tests/foo.rs` (integration) | no — whole file only compiles for tests | **no** — separate crate, only `pub` API | testing the crate as a consumer sees it |
| `/// ```` doc comment | no | only `pub` API (runs as external crate) | examples that must stay correct |
| `benches/` (criterion) | no | only `pub` API | performance, not correctness |

The split is the whole point: **unit tests verify internals, integration tests verify the public contract.** If an integration test needs a private item, that's a signal the item should be `pub` (or the test belongs in `mod tests`).

```rust
// src/lib.rs
fn parse_internal(s: &str) -> Option<u32> { s.strip_prefix("x").and_then(|n| n.parse().ok()) }

#[cfg(test)]              // <- compiled only under `cargo test`; not in the shipped crate
mod tests {
    use super::*;         // pull the parent module's items, including private ones
    #[test]
    fn parses_x_prefix() {
        assert_eq!(parse_internal("x42"), Some(42));
        assert_eq!(parse_internal("42"), None);
    }
}
```

`#[cfg(test)]` on the module is load-bearing: without it, `mod tests` (and its `dev-dependencies` usage) compiles into normal builds and breaks `cargo build`. Put it on the `mod`, not each `#[test]`.

Each file in `tests/` is its own crate (own `extern`, own compile). Shared test helpers go in `tests/common/mod.rs` (the `/mod.rs` form, **not** `tests/common.rs`, so cargo doesn't treat it as its own test target).

## Assertions — pick the one that reports best

- `assert_eq!(a, b)` / `assert_ne!` over `assert!(a == b)` — they print *both* values on failure. `assert!(x == y)` just says "false".
- `assert!(cond, "msg with {x:?}")` — custom message; use captured-identifier formatting (`{x:?}`), not `{:?}`, x.
- `debug_assert!` for invariants you don't want costing release perf — compiled out in release.
- Avoid bare `panic!`/`unwrap()` *as* the assertion when a macro states intent better; reserve them for "this path is unreachable in a correct test."

## `#[should_panic]` vs `Result`-returning tests vs `?`

Three ways a test signals failure. Choose by what you're actually asserting:

```rust
// 1. The behavior under test is *supposed* to panic. Pin the message so you don't
//    accidentally pass on an unrelated panic.
#[test]
#[should_panic(expected = "index out of bounds")]
fn oob_panics() {
    let v = vec![1];
    let i = std::hint::black_box(5); // runtime index — a CONST index like v[5] into a fixed array
    let _ = v[i];                    // is caught by the deny-by-default `unconditional_panic` lint at COMPILE time
}

// 2. The test body uses `?` for setup that *shouldn't* fail. Return Result so `?`
//    works and an Err is a clean failure (no `.unwrap()` noise).
#[test]
fn round_trips() -> Result<(), Box<dyn std::error::Error>> {
    let parsed: u32 = "42".parse()?;
    assert_eq!(parsed, 42);
    Ok(())
}
```

**Anti-pattern:** `#[should_panic]` *without* `expected = "..."` on real code — it passes on *any* panic, including a typo-induced one in your test, giving false confidence. Always pin the substring. And don't reach for `#[should_panic]` when the function returns `Result<_, E>` — assert on the `Err` variant instead (richer, no unwinding):

```rust
#[test]
fn rejects_bad_input() {
    let err = parse_config("").unwrap_err();
    assert!(matches!(err, ConfigError::Empty));
}
```

`Result`-returning tests cannot use `#[should_panic]` together — they're mutually exclusive models.

## Doc tests — examples that can't rot

Code in `///` doc comments is compiled *and run* by `cargo test` as if an external user pasted it. This is the cheapest way to keep examples honest.

````rust
/// Adds one.
/// ```
/// assert_eq!(mycrate::add_one(2), 3);
/// ```
///
/// Hidden setup with `#` lines (not shown in rendered docs but still compiled):
/// ```
/// # use mycrate::Parser;
/// let p = Parser::new();
/// assert!(p.is_empty());
/// ```
pub fn add_one(x: i32) -> i32 { x + 1 }
````

Fence annotations carry meaning: ` ```no_run ` (compile, don't execute — for network/IO examples), ` ```ignore ` (don't even compile — last resort), ` ```compile_fail ` (assert it *doesn't* compile — great for proving a misuse is rejected), ` ```should_panic `, and ` ```text ` (not Rust at all). Prefer `no_run` over `ignore`: `ignore` lets the example bit-rot silently, `no_run` still catches signature drift.

## Table-driven tests — the idiomatic loop

Rust has no parametrized-test attribute in std. The idiom is a slice of cases iterated in one `#[test]`. Include a label so a failure tells you *which* row:

```rust
#[test]
fn slugify_cases() {
    let cases = [
        ("Hello World", "hello-world"),
        ("  trim  ",     "trim"),
        ("a__b",         "a-b"),
    ];
    for (input, want) in cases {
        assert_eq!(slugify(input), want, "input = {input:?}");
    }
}
```

Tradeoff: one `#[test]` means the loop stops at the first failing row (and the test name doesn't name the case). When you want each case as an independently-reported, independently-`--`-filterable test, reach for the `rstest` crate's `#[case(...)]` (verify API with Context7 before use) instead of hand-rolling.

## Third-party tools — what to reach for (verify each with Context7)

> Versions/attribute names drift across major releases. **Confirm current API via Context7 before writing these** — examples below show intent, not pinned signatures.

| Need | Crate | Why this and not the alternative |
|---|---|---|
| Property-based: generate inputs, shrink failures | `proptest` | Integrated shrinking + regression-file persistence; richer strategy DSL. Prefer over `quickcheck` for new code — `quickcheck` is simpler but its shrinking is weaker and it's less actively evolved. |
| Microbenchmarks with statistics | `criterion` | Stable-Rust benches (std `#[bench]` is nightly-only), warmup + outlier detection + regression reports. |
| Snapshot / golden tests | `insta` | `assert_snapshot!`/`assert_yaml_snapshot!` + `cargo insta review` to accept diffs. Use when the *shape* of complex output matters more than hand-writing every field. |
| Mock a trait | `mockall` | Generates a mock impl from a trait. Use **only at genuine seams** (network, clock, external service) — see tradeoff below. |
| Per-case parametrization / fixtures | `rstest` | `#[case]` rows become separate tests; `#[fixture]` for shared setup. |

### proptest — assert a property, let it hunt counterexamples

```rust
use proptest::prelude::*;   // verify proptest API with Context7

proptest! {
    #[test]
    fn parse_is_inverse_of_display(n in 0u32..) {
        // round-trip property: display then parse gets the original back
        prop_assert_eq!(n.to_string().parse::<u32>().unwrap(), n);
    }
}
```

On failure proptest **shrinks** to a minimal counterexample and writes it to `proptest-regressions/` so the exact failing input is replayed on the next run. That regression file is meant to be committed.

### mockall — the tradeoff

Mocking couples the test to *how* the code calls a dependency (expected calls, argument matchers), not *what* it produces. That makes tests brittle under refactor. The idiomatic Rust preference order:

1. **Real implementation** when fast and deterministic (pure logic, in-memory).
2. **A hand-written fake** behind your own trait (often a dozen lines, e.g. an in-memory `Repository` impl) — clearer and refactor-stable.
3. **`mockall`** only when the dependency is a true I/O boundary and a fake is impractical.

If you find yourself mocking your *own* pure types, that's a design smell — extract the pure logic and test it directly.

## Async tests — `#[tokio::test]`

A plain `#[test] async fn` won't compile/run (no runtime, and `#[test]` rejects `async fn`). Use the runtime's test macro:

```rust
#[tokio::test]                          // spins up a current-thread runtime per test
async fn fetches() {
    let n = compute().await;
    assert_eq!(n, 42);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]   // when the test needs real parallelism
async fn concurrent() { /* ... */ }
```

Pitfalls specific to async tests:
- A hung `.await` (deadlock, never-resolving future) makes the test **hang forever**, not fail — wrap suspect awaits in `tokio::time::timeout(...)` so a regression surfaces as a failure.
- For time-based logic, use `tokio::time::pause()` + `advance()` (start the test with `#[tokio::test(start_paused = true)]`) instead of real `sleep` — deterministic and instant.
- The `Send`-across-`.await` rules from `async-tokio.md` still bite in tests; a `!Send` guard held across an await fails the same way.

## Running, filtering, isolating

- `cargo test pattern` — runs only tests whose **path** contains `pattern` (substring, not regex). `cargo test --test foo` runs one integration file; `--doc` runs only doc tests; `--lib` only unit tests.
- `cargo test -- --nocapture` — show `println!`/stdout from passing tests (cargo swallows it by default). The bare `--` separates cargo args from the **test-harness** args.
- `cargo test -- --test-threads=1` — serialize tests. Tests run in parallel by default and **share process state**: a test mutating a shared env var, a global, the cwd, or a fixed port can flake. Fix the *sharedness* (unique temp dirs via `tempfile`, `:0` ephemeral ports) rather than permanently forcing single-threaded.
- `#[ignore]` marks slow/manual tests; run them with `cargo test -- --ignored`.

### `cargo nextest` — the upgrade

`cargo nextest run` runs each test in its **own process**, which (a) isolates global/static state and panics that would poison a shared harness, (b) is faster on large suites, and (c) gives a cleaner per-test report and real per-test timeouts. It does **not** run doc tests — keep `cargo test --doc` for those. Worth adopting for any non-trivial suite; the only friction is process-per-test startup for very many tiny tests.

## Web scenario — test handlers without a live socket

For a web app (Salvo, axum, actix, …) the high-value unit is **the handler/route, exercised in-process** — no bound port, no real network. Web frameworks ship an in-memory test client for exactly this: you build a request, hand it to the service/router, and assert on the response. This is faster and flake-free versus spawning a server and hitting `127.0.0.1`.

The language-level points that carry across frameworks:
- These client calls are `async` → the test is `#[tokio::test]`.
- Drive the app through its `Service`/`Router` value directly so you never bind a socket (and tests can run fully parallel).
- Assert on **status + deserialized body** (parse the JSON into your DTO and `assert_eq!` the struct), not on a brittle raw-string match.
- Share app construction in a `tests/common/mod.rs` helper so every integration file builds the same wired-up app.

The exact client type and request-builder API are framework-specific — for **Salvo's `TestClient`** defer to **salvo-skill**; for axum/actix use their respective test utilities. This skill owns only the `async`/`tokio::test` and assertion shape, not the framework call.
