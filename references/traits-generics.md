# Traits & generics

Assumes **Rust 1.95 / edition 2024**. This file is about *design decisions* — static vs dynamic dispatch, how to phrase bounds, associated types vs type params, blanket impls, `impl Trait` — and the three errors that show up when you get them wrong (E0038, E0277, E0599/E0308). Non-trivial third-party trait APIs (`syn`/`quote`, `thiserror` derives) — verify with Context7 before writing.

## Static (`T: Trait`) vs dynamic (`dyn Trait`) dispatch

This is the central trade-off. Generics get **monomorphized**: the compiler stamps out one copy of the function per concrete type, dispatch is resolved at compile time, calls inline. `dyn Trait` keeps **one** copy and dispatches through a vtable at runtime.

| Want | Use | Why |
|---|---|---|
| Max speed, inlinable, types known at compile time | `fn f<T: Trait>(x: T)` / `impl Trait` | static dispatch, no vtable, monomorphized |
| A **heterogeneous collection** (`Vec<Box<dyn Draw>>`) | `dyn Trait` | can't have a `Vec<T>` holding different `T`s |
| Cut compile time / binary size (many instantiations) | `dyn Trait` | one copy instead of N |
| Return one of several types from a fn, chosen at runtime | `Box<dyn Trait>` | `impl Trait` return must be a *single* concrete type |
| Plugin / trait-object registry, callbacks stored in a struct | `Box<dyn Fn>` / `dyn Trait` | type erasure is the point |

Default to **generics**; reach for `dyn` when you specifically need type erasure (heterogeneous storage, runtime-chosen return) or to tame monomorphization bloat. `dyn` costs a pointer indirection and blocks inlining — usually negligible, but it's a real choice, not a fallback.

```rust
// Static: monomorphized, inlines. One Logger type per call site.
fn log_all<W: std::io::Write>(mut w: W, lines: &[&str]) {
    for l in lines { let _ = writeln!(w, "{l}"); }
}

// Dynamic: heterogeneous storage — the canonical reason to reach for dyn.
trait Draw { fn draw(&self); }
let shapes: Vec<Box<dyn Draw>> = vec![/* Circle, Square, ... */];
for s in &shapes { s.draw(); }   // vtable dispatch
```

## Object safety (E0038) — *why* a trait can't be `dyn`

`dyn Trait` is built behind a thin pointer + vtable, so every method must be callable knowing only "some `Self` of unknown size". A trait is **not** object-safe (the compiler now says "not dyn compatible") when it has:

- a method returning or taking `Self` by value (`fn clone(&self) -> Self`) — size unknown behind the pointer;
- a **generic method** (`fn f<T>(&self, t: T)`) — vtable can't hold infinitely many monomorphizations;
- `Self: Sized` bound on the trait itself, or a `const`/static-dispatch-only item;
- (associated functions without `&self`, associated consts — also block it).

```rust
trait Bad { fn make() -> Self; }       // no &self, returns Self
// let b: Box<dyn Bad> = ...;          // E0038: `Bad` is not dyn compatible
```

**Fixes, in order of preference:**
1. **Don't use `dyn`** — if generics work, the problem vanishes.
2. **Split the trait**: keep object-safe methods in the `dyn`-able trait, move the offending ones into a separate trait or behind `where Self: Sized`:
   ```rust
   trait Animal {
       fn name(&self) -> &str;                  // object-safe
       fn clone_box(&self) -> Box<dyn Animal>;  // erased clone, object-safe
       fn spawn() -> Self where Self: Sized;    // excluded from the vtable
   }
   ```
   The `where Self: Sized` escape hatch tells the compiler "this method only exists for concrete types, don't put it in the vtable" — that's how `Iterator::collect` etc. stay on otherwise-`dyn` traits.
3. For "I need `Clone` on a trait object", the idiom is a `clone_box(&self) -> Box<dyn Trait>` method (or the `dyn-clone` crate — verify API with Context7), because `Clone: Sized` can't go in a vtable directly.

## Trait bounds: inline vs `where`

Same meaning; `where` is for readability and for bounds you can't write inline.

```rust
// inline — fine for one or two simple bounds
fn print_all<T: std::fmt::Display>(xs: &[T]) { /* ... */ }

// where — use when bounds get long, or involve associated types / higher-ranked
fn process<I, T>(it: I) -> Vec<String>
where
    I: IntoIterator<Item = T>,
    T: std::fmt::Display + Clone,
{ it.into_iter().map(|x| x.to_string()).collect() }
```

Only require `where T: Trait` (or `F: Fn(...)`) what the body **actually uses** — over-tight bounds leak into every caller. Bounds you can *only* phrase with `where`: associated-type constraints (`where I::Item: Ord`) and higher-ranked lifetimes (`where F: for<'a> Fn(&'a str)`).

## Associated types vs generic type parameters

Both let a trait abstract over a type. The rule:

- **One impl per `Self` → associated type.** `Iterator` has *one* `Item` per iterator type; you never want `impl Iterator<u8>` *and* `impl Iterator<char>` for the same struct. Associated types make `Item` an *output* the impl determines, so callers write `I::Item` and don't have to spell it.
- **Many impls per `Self` → generic parameter.** `From<T>` / `Add<Rhs>`: a type can be `From<u8>` *and* `From<&str>`; the parameter is an *input* the caller chooses.

```rust
trait Parser {
    type Output;                              // one Output per parser — assoc type
    fn parse(&self, s: &str) -> Self::Output;
}

trait Convert<Into> {                         // many target types per source — generic
    fn convert(&self) -> Into;
}
```

Picking a generic param where an associated type belongs forces callers to annotate a type that was never theirs to choose (and can make the trait non-object-safe to no purpose). Picking an associated type where you need multiple impls is a hard wall — you simply can't write the second impl.

## Blanket impls & the orphan rule

A **blanket impl** implements a trait for every type satisfying a bound — `impl<T: Display> ToString for T`. Powerful, but it's a commitment: it covers types that don't exist yet, so you can't *also* write a more specific overlapping impl (no stable specialization).

The **orphan rule** (E0117/E0210): you may `impl Trait for Type` only if **the trait or the type is local to your crate**. This is what stops two crates from both impl-ing `Display for Vec<T>` and breaking coherence.

```rust
// ❌ both foreign → orphan rule violation (E0117)
// impl std::fmt::Display for Vec<u8> { ... }

// ✅ newtype makes the type local, so the impl is allowed
struct Bytes(Vec<u8>);
impl std::fmt::Display for Bytes {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} bytes", self.0.len())
    }
}
```

## Newtype: the orphan-rule escape hatch + a typing tool

`struct Meters(f64);` does double duty: (1) makes a foreign type *local* so you can impl foreign traits on it; (2) gives the compiler a distinct type so `Meters` and `Seconds` can't be swapped. Cost: you reach the inner value via `.0` or a `Deref` impl. Add `#[derive(...)]` / `#[repr(transparent)]` as needed; reach for `Deref` *only* for genuine smart-pointer-like wrappers — auto-deref on a units newtype re-opens the very mix-ups you created it to prevent.

## `impl Trait` in argument and return position

- **Argument position** `fn f(x: impl Trait)` is exactly sugar for `fn f<T: Trait>(x: T)` — a fresh anonymous generic. Difference: with `impl Trait` you **lose turbofish** (`f::<...>()` is impossible), so use the explicit `<T>` form when callers need to name `T`.
- **Return position** `fn f() -> impl Trait` returns *one* hidden concrete type, statically. Use it to return closures and iterators without boxing:
  ```rust
  fn adder(n: i32) -> impl Fn(i32) -> i32 { move |x| x + n }
  fn evens(max: i32) -> impl Iterator<Item = i32> { (0..max).filter(|x| x % 2 == 0) }
  ```
  You **cannot** return two different concrete types from the branches of one `-> impl Trait` fn (E0308: "if and else have incompatible types"). If branches differ, box it: `-> Box<dyn Trait>`. In edition 2024, RPIT captures all in-scope generic/lifetime params by default — use `+ use<>` to opt out of capturing.

## `Send + Sync + 'static` as a bound — what you're actually demanding

These appear constantly on spawned tasks, trait objects, and shared state. They are **auto traits / a lifetime bound**, and as a bound they mean:

| Bound | Means | Typically required by |
|---|---|---|
| `Send` | ownership can move to another thread | `thread::spawn`, `tokio::spawn` (value moved into task) |
| `Sync` | `&T` can be shared across threads (`T: Sync ⇔ &T: Send`) | sharing `Arc<T>` across threads |
| `'static` | contains no borrows shorter than the whole program — owns its data or holds `&'static` | anything outliving the current stack frame: spawned tasks, `Box<dyn Trait>` defaults, stored callbacks |

`'static` does **not** mean "lives forever" — it means "isn't borrowing anything transient". An owned `String` is `'static`; a `&'a str` is not. `Box<dyn Error>` actually means `Box<dyn Error + 'static>` (elided default), which is why returning a borrow inside it fails. When a closure must be `Send + 'static`, `move` it and ensure every captured value is itself `Send + 'static` (clone an `Arc` in rather than borrowing). See `async-tokio.md` for `Send`-across-`.await`.

## E0277 — "trait bound not satisfied" → root cause, not a reflexive bound

E0277 means a required `T: Trait` isn't met. The message names the trait and type; the fix depends on *which*:

- **Real missing impl** → derive or implement it (`#[derive(Debug, Clone)]`, `impl From<...>`). Most E0277 are this.
- **`?` operator E0277** (`From<E1>` for your error not found) → add a `#[from]` variant / `impl From` so `?` can convert. See `error-handling.md`.
- **Missing `Send`/`Sync`** on a spawned future/value → an inner `!Send` type (e.g. `Rc`, a `MutexGuard` held across `.await`). Fix the *inner* type, don't slap `+ Send` on the signature (you can't — auto traits aren't manually addable here).
- **Orphan-adjacent**: you tried to use a foreign trait on a foreign type via a blanket somewhere → newtype.

The anti-pattern is adding a `where T: SomeTrait` to silence it without asking *who is supposed to provide that impl* — that just pushes E0277 to the call site. Fix it where the type is defined.

## E0599 — "no method named `x` found" → usually a missing `use` for the trait

A trait method is only in scope if the **trait is imported**. `s.parse()`, `bytes.read_exact()`, `it.try_collect()` all fail with E0599 until you `use std::str::FromStr` / `use std::io::Read` / `use itertools::Itertools`. The compiler often hints "method exists but trait not in scope" — that's the tell. Other E0599 causes: the bound that provides the method isn't satisfied (so the impl doesn't apply — go check the bounds), or you called an associated fn with `.` instead of `::`.

## Anti-patterns

- **`Box<dyn Trait>` reflexively** when a generic would compile — you pay a vtable + heap alloc to erase a type you actually know. Box for *heterogeneous storage* or *runtime-chosen return*, not by default.
- **`dyn` to dodge a generic that "feels heavy"** before measuring — monomorphization is usually the *faster* choice; only `dyn` for binary-size/compile-time when it's a measured problem.
- **Generic param where an associated type belongs** (or vice versa) — forces caller annotations or blocks the second impl. Decide by "how many impls per `Self`".
- **`Deref` on a newtype to avoid `.0`** — re-introduces the type confusion the newtype existed to prevent (and leaks the inner API). Add accessor methods instead; reserve `Deref` for smart pointers.
- **Adding `+ Send`/`where T: Send` to silence E0277** — auto traits propagate from the data; fix the `!Send` field, don't annotate around it.
- **Over-tight bounds** (`T: Clone + Debug + Default` when the body only `Display`s) — every bound becomes a caller requirement; demand only what you use.
