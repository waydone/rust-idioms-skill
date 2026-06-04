# Ownership, borrowing & lifetimes

The borrow checker is a *design* tool, not a syntax gate. Most "it won't compile" cases are the compiler refusing a design that's actually unsound (or under-specified) — the fix is to change ownership intent, not to silence it with `.clone()` / `'static` / `Box::leak`. This file is the root-cause map.

## The first question: who *owns* this value?

Before annotating anything, decide the relationship between caller and callee:

| You want callee to… | Pass | Caller afterward | Use when |
|---|---|---|---|
| Take it, store it, drop it, return it elsewhere | `T` (move) | loses access (unless `Copy`) | constructors, builders, `spawn`, channel sends, anything that *keeps* the value |
| Read it, maybe briefly | `&T` | keeps full access | inspection, hashing, formatting, comparisons — the default |
| Mutate it in place, no realloc-of-ownership | `&mut T` | regains access after the borrow ends | in-place edit, `push`, `sort`, accumulating into a passed-in buffer |

Heuristic: **default to `&T`; upgrade to `&mut T` only to mutate; move only to transfer ownership or lifetime.** Taking `T` "to be safe" forces callers to clone — that's the inverted version of the clone anti-pattern. A function that only reads should take `&str` / `&[T]` / `&T`, never `String` / `Vec<T>` / `T`.

Corollary for APIs: accept the *borrowed/unsized* form (`&str`, `&[u8]`, `&Path`, `impl AsRef<str>`), not the owned one, so callers choose whether to clone.

## `Copy` / `Clone` — the real cost

- `Copy` = a bitwise `memcpy`, no allocation, no destructor. Only for small POD-ish types (`u32`, `bool`, `char`, `(f64, f64)`, small `#[derive(Copy)]` structs). Passing/returning these by value is free; references to them are usually *pessimization*.
- `Clone` = arbitrary user code. For `Vec`/`String`/`HashMap` it's a heap allocation + element-wise copy — O(n). For `Arc<T>`/`Rc<T>` it's just an atomic/non-atomic refcount bump (cheap), **not** a deep copy. So `arc.clone()` is fine and idiomatic; `big_vec.clone()` in a hot loop is a smell.
- Don't `#[derive(Copy)]` to dodge move errors — it changes value semantics (now everything is an implicit copy) and only works if every field is `Copy`. Fix the borrow instead.
- "Clone to satisfy the borrow checker" is the canonical wrong fix. A clone is right when you genuinely need a second independent owner (e.g. moving data into a spawned task while keeping the original). It's wrong when you only needed to *read* — that means you should have borrowed.

## Shared/owned wrapper selection

| Wrapper | Gives you | Cost | Reach for it when |
|---|---|---|---|
| `Box<T>` | single owner, heap, fixed-size handle | one alloc | recursion (`enum Tree { Node(Box<Tree>) }`), `dyn Trait`/`impl Trait` storage, moving large values cheaply, breaking a sized cycle |
| `Rc<T>` | shared ownership, single-thread | refcount (non-atomic) | many readers in one thread (graphs, ASTs); **not `Send`** |
| `Arc<T>` | shared ownership, cross-thread | atomic refcount | shared immutable state across tasks/threads (Salvo handler state, config) |
| `Arc<Mutex<T>>` / `Arc<RwLock<T>>` | shared *mutable* state | atomics + lock | last resort; prefer message-passing or `&mut` ownership. For async use `tokio::sync::Mutex` only if the guard crosses `.await` |
| `Cow<'a, T>` | borrow *or* own, decided at runtime | branch | API returns mostly-borrowed data, occasionally owned (e.g. unescape that usually no-ops) |
| `Rc<RefCell<T>>` | shared mutable, single-thread | runtime borrow check (panics on violation) | observer/graph mutation in one thread; moves E0502 from compile-time to runtime — use sparingly |

Notes that bite: `Arc` does **not** give mutability — `Arc<T>` is read-only; you need `Arc<Mutex<T>>` (or atomics, or `Arc<T>` where `T` has interior mutability) to mutate. `Rc`/`RefCell` are `!Send`, so they'll surface as "future is not Send" the moment they cross an `.await` (see `async-tokio.md`). Prefer immutable `Arc<T>` + rebuild-and-swap over `Arc<Mutex<T>>` when reads dominate.

### `Cow` in practice (fragment, std-only)

```rust
use std::borrow::Cow;

// Borrows when there's nothing to fix; allocates only on the rare path.
fn normalize(input: &str) -> Cow<'_, str> {
    if input.contains(' ') {
        Cow::Owned(input.replace(' ', "_")) // allocate only here
    } else {
        Cow::Borrowed(input)                // zero-copy common case
    }
}
```

## Lifetimes: annotation & elision

A lifetime annotation never changes how long anything lives — it only *describes* a relationship the compiler can't infer. You write one when a function/struct ties an output reference to an input reference.

**Elision rules** (why most signatures need none): (1) each elided input ref gets its own lifetime; (2) if there's exactly one input lifetime, it's assigned to all outputs; (3) with `&self`/`&mut self`, `self`'s lifetime is assigned to all outputs. When these can't pick a single source — e.g. two `&str` inputs and a `&str` output — you must annotate to say *which* input the output borrows from.

```rust
// Must annotate: output borrows from `a`, not `b`. Elision can't choose.
fn left<'a>(a: &'a str, _b: &str) -> &'a str { a }
```

Structs holding references need a lifetime param: `struct Parser<'a> { src: &'a str }` — and the struct can't outlive `src`. If that fights you everywhere, the design answer is usually *own the data* (`String`) instead of borrowing it.

## `'a` vs `'static` — what `'static` actually means

`'static` means "this reference is valid for the entire program" (string literals, leaked allocations) **or**, as a *bound* (`T: 'static`), "this type contains no non-`'static` references" — i.e. it's fully owned. `String`, `Vec<u8>`, `Arc<Config>` are all `T: 'static` despite being heap-allocated and dropped normally; the bound is about *borrowed* content, not lifetime of the value.

You genuinely need `'static` for: `thread::spawn` / `tokio::spawn` closures (the task may outlive the spawner), `Box<dyn Trait + 'static>` trait objects you store, and `dyn Any`. You do **not** need it just because the compiler suggested it on an error — that suggestion is often the compiler giving up; the real fix is usually to **own the data** (return `String` not `&str`) or to thread a real `'a` through. Reaching for `'static` to silence E0597/E0515 typically forces `Box::leak` or `Arc` cascades; treat it as a redesign signal, not a fix.

## Error code → design problem → root-cause fix

| Code | What it means | Underlying design problem | Root-cause fix (not `.clone()`) |
|---|---|---|---|
| **E0382** | use of moved value | you moved it, then used the original | Borrow instead of move (`&x`) if the callee only reads; or restructure so the last use *is* the move. Clone only if you truly need two owners. |
| **E0499** | two `&mut` to the same value at once | aliased mutation — unsound by definition | Split the borrows: scope the first `&mut` so it ends before the second; use `split_at_mut` / `iter_mut` / indices for disjoint parts of a collection; pull the shared field into a separate variable. |
| **E0502** | `&` and `&mut` overlap | reading and writing the same data simultaneously | End the shared borrow before mutating (NLL shrinks borrows to last-use — often just reorder). For self-referential reads-then-writes, copy out the needed value first, then mutate. |
| **E0505** | move while borrowed | you moved a value that's still lent out | Drop/scope the borrow before the move, or move *before* you create the borrow. If a method takes `self` by value while a field is borrowed, take what you need first. |
| **E0506** | assign to a borrowed value | reassigning storage someone still references | Let the borrow end first; if it's a loop accumulator, restructure so the borrow doesn't span the reassignment. |
| **E0507** | move out of borrowed content / behind a ref | trying to take ownership through `&` (incl. out of `Vec[i]`, `HashMap`, `RefCell`) | If you only read, borrow safely (`words.first()` / `if let Some(first) = words.first()`) instead of indexing (`&words[0]` panics when empty). If you own the `Vec` and want first + rest, consume it with `into_iter()` + `next()`. Use `mem::take`/`replace` for fields behind `&mut`, `Option::take` for optional fields, and `remove` only when you mean to mutate the container. |
| **E0515** | return reference to local | returning a borrow of a value that dies at function end | Return the **owned** value (`String`, `Vec<T>`), or accept an output buffer `&mut`, or return `Cow`. The function can't lend out something it allocated and drops. |
| **E0596** | `&mut` of an immutable binding | you need to mutate but didn't ask for mutability | Add `mut` to the binding, or change the receiver/param to `&mut self` / `&mut T`. If it's behind `&self` and must mutate, that's a design choice → interior mutability (`RefCell`/`Mutex`/atomics), used deliberately. |
| **E0597** | borrowed value dropped too soon | the referent's scope is shorter than the reference | Hoist the owner to an outer scope so it lives long enough; bind temporaries to a `let` (avoid borrowing a value created inline that drops at `;`); or own the data instead of borrowing. **Not** `'static`. |
| **E0716** | temporary freed while borrowed | you borrowed a temporary that drops at the end of the statement | `let tmp = expr;` to extend the temporary's lifetime, then borrow `tmp`. Common with `.lock().unwrap()` chains and format temporaries. |

`E0499`/`E0502` together are the "I'm trying to mutate a collection while iterating/indexing it" family — the answer is almost always *disjoint access* (`split_at_mut`, `iter_mut`, index math, or a second pass), not a `RefCell`.

## Idiomatic vs merely-compiles

- **`mem::take` over clone-then-overwrite.** To move a field out behind `&mut self`: `let v = std::mem::take(&mut self.items);` (leaves `Vec::default()`), not `self.items.clone()` then ignore.
- **First element: borrow safely or consume deliberately.** For read-only access, prefer `words.first()` / `if let Some(first) = words.first()`; `&words[0]` is a panic path on empty input. If you own the `Vec` and need an owned first item plus the rest consumed, use `let mut it = words.into_iter(); let Some(first) = it.next() else { return; }; for w in it { ... }`. After a prior non-empty proof, the compact form is `let first = it.next().unwrap();`. `remove(0)` shifts all remaining elements and mutates the `Vec`; `mem::take` leaves a `Default` placeholder for a field behind `&mut`, not a clean split of first/rest.
- **Two `&mut` into one slice → `split_at_mut`**, not `RefCell`. The borrow checker can't prove `a[i]` and `a[j]` are disjoint; `split_at_mut` (or `iter_mut`) proves it for you with zero runtime cost.
- **Return owned, borrow on input.** `fn build() -> String` (owns its result) but `fn parse(s: &str)` (borrows input). Mixing this up (`fn build() -> &str` of a local) is the direct cause of E0515.
- **Accept `&str`/`&[T]`, not `String`/`Vec<T>`,** for read-only params — and `impl AsRef<Path>` / `impl Into<String>` when you want ergonomic call sites.
- **Reorder for NLL.** Non-lexical lifetimes end a borrow at its last *use*, not end of scope. Moving the mutating line below the last read often fixes E0502 with no structural change — try that before reaching for `Cell`/`RefCell`.

## Anti-patterns (the silencers)

- `.clone()` to dodge E0382 when you only read → should have borrowed; the clone is a hidden O(n) alloc.
- Sprinkling `'static` to dodge E0597/E0515 → forces `Box::leak`/`Arc` and leaks memory; you wanted owned data or a real `'a`.
- `Arc<Mutex<T>>` as the default for any shared state → lock contention + deadlock risk; prefer immutable `Arc<T>`, message passing, or `&mut` ownership. (In async, a `std::sync::MutexGuard` held across `.await` makes the future `!Send` — see `async-tokio.md`.)
- `Rc<RefCell<T>>` to escape the borrow checker → moves aliasing bugs from compile time to runtime panics; use only for genuine single-thread shared-mutable graphs.
- `#[derive(Copy)]` to make move errors disappear → changes semantics silently and only papers over a borrow you should have written.

> Version: assumes Rust 1.95 / edition 2024; all snippets here are std-only and need no crate. For wrapper-vs-async interactions (`!Send` across `.await`) see `async-tokio.md`; for `dyn`/object-safety lifetime bounds see `traits-generics.md`.
