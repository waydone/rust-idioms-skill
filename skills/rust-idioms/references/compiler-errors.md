# Compiler errors — `E0xxx` fast lookup

A diagnosis index, not a tutorial. Each entry: **what the compiler is actually telling you** → **the minimal correct fix** → **whether the error is a smell that you should redesign rather than patch.** When the deeper topic matters, the row points at the sibling reference; this file is the triage front-door.

The trap to avoid: most of these have a *silencing* move (`.clone()`, `'static`, `Box::leak`, `unsafe`, `unwrap`) that makes the message disappear without addressing the design. The "redesign?" column flags when the silencing move is the wrong instinct.

## Ownership / borrow / lifetime cluster

These all come from one model: a value has one owner; you may have *many* `&T` **xor** *one* `&mut T`; a reference must not outlive its referent. Full reasoning in `ownership-borrowing-lifetimes.md`.

| Code | Compiler is saying | Minimal correct fix | Redesign smell? |
|---|---|---|---|
| **E0382** | use of moved value | borrow (`&x`) instead of consuming; or `clone()` if you genuinely need two owners; or restructure so the move happens last | If you reach for `.clone()` reflexively in a hot path — the data probably wants `&`, `Rc`/`Arc`, or to be *returned* rather than re-read. |
| **E0499** | two `&mut` to the same value at once | split the borrows in time (NLL ends a borrow at last use), or split the *data* (`split_at_mut`, separate fields, indices) | Repeated E0499 on one struct ⇒ the struct wants field-level borrows or an index-based design, not one big `&mut self` method. |
| **E0502** | `&` and `&mut` overlap | finish using the shared borrow before taking `&mut`; clone the small read-out value; or copy out an owned key before the mutating call | Common with `map.get(&k)` then `map.insert(...)`: use the entry API (`map.entry(k)`) — that's the idiom, not a clone. |
| **E0503** | use of a value while it's mutably borrowed | let the `&mut` end (drop/last-use) before reading | Usually NLL solves it by reordering; persistent ⇒ same split-the-borrow story as E0499. |
| **E0505** | move out of a value that is borrowed | move *after* the borrow ends; or hand out an owned clone instead of moving the original | Often a sign an API takes `self` when it should take `&self`. |
| **E0506** | assign to a variable that is borrowed | end the borrow before reassigning | Frequently a loop holding a borrow across an iteration that also mutates — restructure the loop body. |
| **E0507** | move out of borrowed content (`&`/`&mut`/index/`Vec`) | borrow if you only read (`first()` instead of `[0]`); consume the owner if you need ownership (`into_iter()` + `next()`); or use `mem::take`/`replace`, `Option::take`, `Vec::remove`/`swap_remove`/`drain` when you must leave valid storage behind | Moving out of `self.field` in a `&mut self` method ⇒ `Option<T>` + `take()`, or `mem::replace`, is the idiom. |
| **E0515** | returning a reference to a local (`cannot return value referencing local variable`) | return the owned value (`String`/`Vec`/`T`), not `&` to it; or `Cow`; or restructure so the caller owns the buffer and you write into it | The classic "I want a getter that computes" — return owned or `impl Iterator`, don't fight to borrow a temporary. |
| **E0596** | `cannot borrow as mutable` | make the binding `let mut`; take `&mut self`; or for interior mutability use `Cell`/`RefCell`/`Mutex` | If the field is behind `&self` and "needs" mutation, decide deliberately: interior mutability (`RefCell`/atomics) vs. genuinely needing `&mut self`. Don't sprinkle `RefCell` to dodge the borrow checker. |
| **E0597** | borrowed value does not live long enough | extend the owner's scope (bind the temporary to a `let`); store the owned value, not a borrow; or invert ownership so the long-lived side owns the data | Self-referential struct (struct holding a `&` into its own field) ⇒ **redesign**: use indices/IDs, `Rc`, `ouroboros`, or owned data. Do not chase it with lifetimes. |
| **E0716** | temporary dropped while borrowed | bind the temporary to a named `let` so it lives long enough (`let v = make(); use(&v);`) | Edition 2024 changed `if let`/tail temporary scopes — a borrow that compiled on 2021 can hit E0716 on 2024; the `let` binding is the fix, not reverting edition. |

### E0507 — the move-out-of-borrow toolkit (most-asked of the cluster)

```rust
use std::mem;

struct Conn { socket: Option<Socket> }

impl Conn {
    // WRONG: cannot move `self.socket` out of `&mut self`  -> E0507
    // fn take_socket(&mut self) -> Socket { self.socket }

    // RIGHT: leave a valid value behind
    fn take_socket(&mut self) -> Socket {
        self.socket.take().expect("socket already taken")   // Option::take leaves None
    }
}

# struct Socket;
// For non-Option fields: mem::replace / mem::take(if Default)
fn swap_field(buf: &mut String) -> String { mem::take(buf) }     // leaves String::new()
```

`for x in &v` borrows; `for x in v` consumes. If you own a `Vec` and want the first owned item plus the rest, use `let mut it = v.into_iter(); let Some(first) = it.next() else { return; }; for x in it { ... }` rather than `remove(0)`. Matching a borrowed option is fine (`if let Some(s) = &opt` or `opt.as_ref()` gives `&T`); E0507 appears when you force by-value extraction from borrowed storage (`*r`, `v[i]`, a `RefCell` borrow).

## Trait / generics cluster

Full treatment in `traits-generics.md`. The two big ones:

| Code | Compiler is saying | Minimal correct fix | Redesign smell? |
|---|---|---|---|
| **E0277** | `T: SomeTrait` is not satisfied | add the bound (`where T: Trait`), import/derive the trait, or implement it; for `?` mismatches add a `From<E1> for E2` | A wall of trait-bound errors after `tokio::spawn`/Salvo handler ⇒ a `Send`/`'static` problem (see below), not a missing `impl`. |
| **E0038** | trait is not object-safe / `dyn Trait` not allowed | use a generic (`fn f<T: Trait>`) for static dispatch; or make the trait object-safe (move generic/`Self`-returning methods to `where Self: Sized`, no `async fn` in the trait for `dyn`) | If you *need* `Vec<Box<dyn Trait>>` but the trait has generic or `Self`-returning methods, the trait is doing two jobs — split it, or switch the collection to an enum. |

### E0277 sub-case: `Send`/`Sync`/`'static` (the async + spawn version)

This is the most common E0277 in real code. The compiler message often reads `future cannot be sent between threads safely` or `... is not Send`, with a note pointing at a value held **across an `.await`**.

```rust
use std::rc::Rc;

// FAILS E0277: future is not Send — `Rc` (or MutexGuard, RefCell::borrow, raw ptr)
// is held alive across the .await point.
async fn bad() {
    let local = Rc::new(1);
    do_io().await;          // `local` is still alive here -> future: !Send
    println!("{local}");
}

// FIX A (idiomatic): drop the !Send value before the await — narrow its scope
async fn good_scope() {
    {
        let local = Rc::new(1);
        println!("{local}");
    }                       // dropped before await
    do_io().await;
}
// FIX B: use a Send type (Arc instead of Rc; tokio::sync::Mutex instead of std + holding guard)
# async fn do_io() {}
```

Root cause is almost never "add `+ Send` somewhere" — it's a non-`Send` guard/`Rc`/`RefCell` ref *living past* a yield point. Fix the holding, not the signature. Details + the spawn/spawn_blocking decision in `async-tokio.md`.

`'static` in `tokio::spawn(fut)` means the future may not borrow from the caller's stack — move owned data in (`move` + `Arc::clone`), don't extend lifetimes to `'static`.

## Type mismatch

| Code | Compiler is saying | Minimal correct fix | Redesign smell? |
|---|---|---|---|
| **E0308** | mismatched types | convert at the boundary: `.into()`/`as`/`.to_string()`/`String::from`; deref/ref to match `&T` vs `T`; `?` for `Result`/`Option`; map the error type | Repeated `.into()`/`as` chains ⇒ wrong type chosen upstream (e.g. `i32` vs `usize`, `&str` vs `String` in a struct field). Fix the type at its source. |

E0308 is the most frequent error overall; the message is usually precise. Read the `expected … found …` line literally. Frequent shapes:

- `expected &str, found String` → borrow it (`&s`) or the function should take `impl AsRef<str>`.
- `expected (), found Result<_, _>` → you dropped a `Result`; add `?` (and make the fn return `Result`) or `.unwrap()`/handle it. *Don't* paper over with `let _ =` if the error matters.
- `if`/`match` arms disagree → both arms must yield the same type; an early `return`/`continue`/`panic!` arm has type `!` and unifies with anything.
- `expected closure, found a different closure` → two closures never share a type; box them (`Box<dyn Fn(..)>`) or use generics.

## Resolution / path cluster

| Code | Compiler is saying | Minimal correct fix | Redesign smell? |
|---|---|---|---|
| **E0432** | unresolved import | fix the `use` path; add the dep to `Cargo.toml`; the item may be behind a Cargo **feature** (enable it) or not re-exported (`pub use`); check `crate::`/`super::`/`self::` prefix | A `use` that "should work" but doesn't is often a missing feature flag — check the crate's `[features]` before assuming a typo. |
| **E0433** | failed to resolve / use of undeclared crate or module | `use` the path or write it fully-qualified; declare the `mod`; add/`extern` the crate; on edition 2024 a bare crate name in a path needs to be a real dependency | New module file not picked up ⇒ you forgot `mod foo;` in the parent. The file existing isn't enough. |
| **E0599** | no method / variant / associated item found | the method comes from a **trait that isn't in scope** — `use` it (e.g. `std::io::Write`, `itertools::Itertools`, `futures::StreamExt`); or you're calling it on the wrong type; or a needed bound is missing | "method exists but trait not imported" is the #1 cause. Before assuming the method is gone, grep for the trait and `use` it. Extension-trait methods (`StreamExt`, `Itertools`) always need the trait imported. |
| **E0106** | missing lifetime specifier | name the lifetime (`fn f<'a>(x: &'a str) -> &'a str`); for structs holding refs, add `struct S<'a> { x: &'a str }`; or sidestep by owning the data (`String` instead of `&str`) | A struct that wants `&` fields and breeds lifetime params everywhere ⇒ consider owning the data or using `Arc`/an arena/indices. Lifetimes in *return position* of a fn are usually fine; lifetimes metastasizing through your *types* are a design signal. |

### E0106 — elision vs. when you must name it

```rust
// Elision handles this — no annotation needed (one input ref -> output ref):
fn first_word(s: &str) -> &str { s.split(' ').next().unwrap_or("") }

// E0106 here: two input refs, compiler can't guess which the output borrows from.
// Name it to state the contract:
fn longest<'a>(a: &'a str, b: &'a str) -> &'a str {
    if a.len() >= b.len() { a } else { b }
}

// E0106 on a struct: a struct that stores a reference MUST carry its lifetime.
struct Parser<'a> { input: &'a str }
```

If naming the lifetime forces it to spread across half your codebase, that's the redesign signal — own the data (`String`/`Vec<T>`/`Arc<str>`) or use indices into a central store.

## Triage flow

1. **Read `expected`/`found` and the `note:` lines first** — rustc usually names the exact value, span, and often a `help:` with the fix. The error *code* is the coarse bucket; the note is the answer.
2. **Map the bucket** with the tables above to the underlying design question.
3. **Resist the silencer.** `.clone()`, `'static`, `Box::leak`, `unsafe`, `unwrap`, `let _ =`, `#[allow(...)]` each have a legitimate use and a lazy use. If you can't say *why* the silencer is correct here, it's masking the real fix (see `anti-patterns.md`).
4. **`cargo check` after the fix**, and `rustc --explain E0XXX` for the canonical writeup. Anti-patterns this triage points away from are catalogued in `anti-patterns.md`; the per-cluster deep dives are the sibling references named in each section.

> Version note: examples target **Rust 1.100 / edition 2024**. The edition-2024 temporary-scope and `if let` changes can surface fresh **E0716/E0597** in code that compiled on 2021 — that's expected; bind the temporary, don't downgrade the edition.
