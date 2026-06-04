# Iterators & closures

Two adjacent topics that share one theme: **the type you reach for changes the cost and the borrow-checker outcome**. The compiler errors here are rarely "you typed it wrong" — they're "you picked the wrong abstraction for the lifetime/ownership you have." This file is about making that pick on purpose. (Assumes Rust 1.95 / edition 2024.)

## Adapter chains are lazy and zero-cost — until you force them

An iterator adapter (`map`, `filter`, `take`, `flat_map`, …) builds a *type*, not a computation. `v.iter().map(f).filter(g)` is a `Filter<Map<Iter<…>>>` that has done **nothing** yet. Work happens only when a *consumer* pulls items: `for`, `collect`, `sum`, `fold`, `find`, `any`, `count`, `for_each`, `try_fold`, etc.

Consequences worth internalizing:

- **Laziness lets you compose infinite/expensive sources cheaply.** `(0..).map(expensive).take(3)` evaluates `expensive` exactly 3 times. Build the pipeline, bound it with `take`/`find`, then consume.
- **Zero-cost is real but conditional.** A monomorphized adapter chain over a slice optimizes to the same machine code as a hand-written loop — *because the closures are statically known*. The moment you box (`Box<dyn FnMut…>`) or insert a `dyn Iterator`, you trade that for a vtable call per item.
- **A chain with no consumer is a bug.** `#[must_use]` fires: `v.iter().map(|x| do_side_effect(x));` does nothing. Use `for_each` (or a `for` loop) when you want the side effects.

## `collect`: the turbofish, and why the target type is everything

`collect::<B>()` is generic over the *result* type `B: FromIterator<Item>`. The iterator's `Item` plus your chosen `B` together determine behavior — sometimes drastically.

| You want | Write | Note |
|---|---|---|
| a `Vec` | `.collect::<Vec<_>>()` or annotate the binding | most common; `_` lets inference fill the element |
| `Result<Vec<T>, E>` from `Iterator<Item = Result<T, E>>` | `.collect::<Result<Vec<_>, _>>()` | **short-circuits** on the first `Err` |
| `Option<Vec<T>>` from `Iterator<Item = Option<T>>` | `.collect::<Option<Vec<_>>>()` | short-circuits on first `None` |
| a `HashMap`/`BTreeMap` | `.collect::<HashMap<K, V>>()` | item must be `(K, V)` |
| a `String` | `.collect::<String>()` | item is `char` or `&str`/`String` |
| group/count | `.fold(HashMap::new(), …)` or `itertools::into_group_map` | no std one-liner |

Two pivotal points:

- **`Result`/`Option` collecting transposes and short-circuits.** This is the idiomatic way to "do N fallible things, fail on the first error" — far cleaner than a manual loop with early `return Err`:

  ```rust
  // fragment — needs: fn parse(s: &str) -> Result<i32, std::num::ParseIntError>
  let nums: Result<Vec<i32>, _> = ["1", "2", "x"].iter().map(|s| s.parse::<i32>()).collect();
  // nums == Err(..) — stops at "x", never parses past it
  ```

- **Prefer annotating the binding over the turbofish when it reads cleaner.** `let v: Vec<_> = it.collect();` and `let v = it.collect::<Vec<_>>();` are equivalent; pick whichever keeps the type near where a reader looks for it. Reach for turbofish when there's no binding to annotate (e.g. `it.collect::<Vec<_>>().join(",")`).

## `Fn` / `FnMut` / `FnOnce`: pick by what the body does to captures

These are a subset hierarchy: every `Fn` is also `FnMut` is also `FnOnce`. A closure implements the **most permissive** trait its body allows; you constrain by what your API stores/calls.

| Trait | Closure body … | Can be called | Use as bound when |
|---|---|---|---|
| `FnOnce` | **consumes** a capture (moves it out, e.g. returns owned `s`) | once | callback runs at most once (`thread::spawn`, `Option::map`) |
| `FnMut` | **mutates** a capture (`counter += 1`, `vec.push`) | many times, needs `&mut` | stateful callback called repeatedly (`Iterator::map`, `retain`) |
| `Fn` | only **reads** captures (or captures nothing) | many times, via `&` | called concurrently / multiple `&`-callers (`rayon`, stored in `Arc`) |

How to think about it: **the bound is a contract about call frequency and aliasing**, not about the closure syntax. Annotate parameters with the *loosest* bound your function actually needs — taking `F: Fn()` when `FnOnce` suffices needlessly rejects valid callers.

```rust
// fragment — loosest-bound API: accepts move-consuming closures too
fn run_once<F: FnOnce() -> String>(f: F) -> String { f() }
fn run_each<F: FnMut(u32)>(mut f: F) { for i in 0..3 { f(i); } } // note: `mut f`
```

`FnMut` parameters must be bound `mut` to call them (the call needs `&mut`). Forgetting that yields **E0596** ("cannot borrow `f` as mutable").

## `move`: capture-by-value, decided per-variable

`move` forces each captured place to be taken by value (moved/copied in). Without it, the closure captures by the least-owning mode each use requires (`&`, then `&mut`, then by value). Since edition 2021 captures are **disjoint** for ordinary structs — a `move` closure that only touches `s.field` can move just that field path, not all of `s` (packed structs and `Drop` types are more conservative).

You need `move` whenever the closure **outlives the captured binding's scope**: spawned threads/tasks, returned closures, anything stored beyond the current frame.

```rust
// fragment
let name = String::from("ada");
let handle = std::thread::spawn(move || println!("{name}")); // without move: E0373
handle.join().unwrap();
```

`move` + a value you still need afterward = clone *into* the closure deliberately:

```rust
// fragment — needs: use std::sync::Arc;
let shared = Arc::new(vec![1, 2, 3]);
let c = Arc::clone(&shared);
std::thread::spawn(move || { let _ = c.len(); }); // c moved in; `shared` still usable here
```

That `Arc::clone` is the *right* clone (cheap refcount bump), not the lazy clone the standing rules warn against.

## Borrowing captures → lifetime / borrow errors (the common traps)

Closures capture references with the **lexical region of the closure value**, which the borrow checker tracks like any other borrow. The recurring failures:

- **E0373 / "may outlive borrowed value"** — closure escapes the frame (thread, returned `impl Fn`, stored in a struct) while holding a `&` to a local. Fix: `move` so it captures owned data (clone or transfer ownership in).
- **E0502 (immutable+mutable) inside iterator chains** — e.g. capturing `&mut acc` in a `map` closure while also iterating `&acc`. Fix: restructure to `fold`, which threads the accumulator as an owned value instead of a captured `&mut`.
- **Capturing `&self` in a returned closure** ties the returned closure's lifetime to `self`. Either return `impl Fn() + '_` (caller must keep `self` alive) or `move` owned/`Arc`'d state into it so it's `'static`.
- **`FnMut` closure can't be called while a capture is borrowed elsewhere.** The closure holds the `&mut`; a second access is E0499. Narrow the closure's capture (touch fewer paths) or split the borrows.

Root-cause framing: a borrow error on a closure almost always means *the closure's lifetime exceeds what it borrows*. The fix is to change **what it owns** (`move`, clone-in, `Arc`), not to sprinkle `'static` bounds until it compiles.

## When to return `impl Iterator`

Returning iterators by value keeps laziness and avoids allocation. Decision order:

1. **`-> impl Iterator<Item = T>`** — default for returning an adapter chain. Zero-cost, no boxing. In edition 2024, RPIT captures all in-scope lifetimes/type params by default; add `+ use<>` to *opt out* of capturing (`-> impl Iterator<Item = i32> + use<>` for a fully owned, `'static`-able iterator). Use `+ '_` / `+ use<'a>` when it borrows from an input.
2. **`-> Box<dyn Iterator<Item = T> + '_>`** — only when you must return *different* concrete iterator types from different branches (RPIT requires one concrete type), or store heterogeneous iterators in a collection. Costs an allocation + per-item vtable dispatch.
3. **Return a `Vec<T>`** — only when the caller genuinely needs an owned, indexable, reusable collection, or when borrows would otherwise force an awkward lifetime. Don't `collect` just to hand back something the caller is about to iterate once.

```rust
// fragment — borrows from input, so the iterator carries that lifetime
fn evens(v: &[i32]) -> impl Iterator<Item = i32> + '_ {
    v.iter().copied().filter(|n| n % 2 == 0)
}
```

Note `.copied()` (or `.cloned()`) to yield `i32` from `&i32` — choosing the iterator's `Item` is part of the API design, not an afterthought.

## Avoiding needless `collect` / `clone`

These are the highest-frequency non-idiomatic patterns in iterator code. Each "compiles" but allocates or copies for no reason.

| Anti-pattern | Why it's wasteful | Idiomatic |
|---|---|---|
| `let v: Vec<_> = it.collect(); for x in v { … }` | allocates a Vec just to drain it once | `for x in it { … }` — iterate directly |
| `it.collect::<Vec<_>>().len()` | materializes to count | `it.count()` |
| `it.collect::<Vec<_>>().into_iter().filter(…)` | round-trips through a Vec mid-chain | keep one lazy chain: `it.filter(…)` |
| `.map(|x| x.clone())` over `&T` | clones every element | `.cloned()` / `.copied()` (signals intent; same cost but clearer), or borrow if the consumer allows |
| `.iter().map(|s| s.to_string()).collect::<Vec<_>>().join(",")` | extra Vec of Strings | `.iter().map(String::as_str).collect::<Vec<_>>().join(",")`, or `itertools::join` to skip the Vec |
| collecting into a temp `Vec` to pass to a fn taking `impl IntoIterator` | the fn already accepts the iterator | pass the iterator/chain directly |
| `for &x in &v { sum += x; }` reimplementing a fold | hand-rolled reduction | `let sum: i32 = v.iter().sum();` |

The unifying rule: **`collect` is a phase change** (lazy → materialized). Do it once, as late as possible, only when something downstream needs a concrete container (indexing, repeated iteration, returning ownership, a `&[T]`/`&str` API). A `collect` in the *middle* of logic is the smell.

One legitimate `collect`-in-the-middle: when you must **end a borrow** before the next step (e.g. collect keys-to-delete into a `Vec` so you can then `&mut` the map). There the allocation buys you a borrow-checker-satisfying ownership boundary — and that's the right call; say so when you write it.

## Quick reference: consumers worth knowing

- Short-circuiting: `find`, `position`, `any`, `all`, `try_fold`, `try_for_each` — stop early, don't traverse the rest.
- Reduction: `sum`, `product`, `fold`, `reduce` (no initial value, returns `Option`), `min`/`max`/`min_by_key`.
- Inspection without consuming: `peekable()` (lookahead), `inspect(|x| …)` (debug taps, stays lazy).
- Index pairing: `enumerate()`; sliding/chunking over slices: `windows`/`chunks` (slice methods, not iterator adapters).
- `zip` stops at the shorter side; `chain` concatenates; `flatten`/`flat_map` un-nest.

For anything from `itertools` (`group_by`, `chunk_by`, `dedup`, `cartesian_product`, `join`), verify the current API with Context7 — its surface has shifted across releases.
