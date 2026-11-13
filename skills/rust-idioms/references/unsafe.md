# Unsafe & FFI

`unsafe` doesn't disable the borrow checker or "turn off safety" — it lets you do five things the compiler can't verify: deref a raw pointer, call an `unsafe` fn, access/modify a `static mut` or `union` field, implement an `unsafe` trait, and declare foreign items in an `unsafe extern` block (edition 2024). Everything else still gets checked. Your job in an `unsafe` block is to **manually uphold the invariants the compiler normally guarantees** — and to write down *which* invariants and *why* they hold.

## When `unsafe` is legitimate (and when it's a smell)

| Situation | Verdict |
|---|---|
| FFI — calling C / system libraries, exporting Rust for C | Legitimate; unavoidable |
| Low-level abstraction the std type system can't express (custom allocator, lock-free structure, intrusive list) | Legitimate; wrap in a safe API |
| **Measured** perf bottleneck where bounds checks / a copy dominate a hot loop (profiled, not guessed) | Legitimate *after* you have the profile |
| "The borrow checker won't let me" / silencing E0382/E0499/E0502 | **Smell.** Redesign ownership; `unsafe` here is how you ship UB |
| `transmute` to change a lifetime or "cast" between unrelated types | **Smell.** Almost always the wrong tool — see below |
| Skipping bounds checks "because it's faster" with no benchmark | **Smell.** `get_unchecked` saves nothing if the compiler already elided the check |

Rule of thumb: `unsafe` should be **rare, small, and encapsulated**. If it's spreading through your codebase, the abstraction boundary is in the wrong place.

## `SAFETY` comments and `/// # Safety` are not optional

Two distinct, both-required conventions (clippy's `undocumented_unsafe_blocks` and `missing_safety_doc` enforce them):

- **Every `unsafe { }` block** gets a `// SAFETY:` comment stating why the invariants hold *here*.
- **Every `unsafe fn` and `unsafe trait`** gets a `/// # Safety` doc section stating the contract the **caller** must uphold.

```rust
/// Returns the element at `idx` without bounds checking.
///
/// # Safety
/// `idx` must be `< self.len()`. Out-of-bounds is undefined behavior.
pub unsafe fn get_unchecked(&self, idx: usize) -> &T {
    // SAFETY: caller guarantees idx < len (documented above), so the
    // pointer is in-bounds and points to an initialized T.
    unsafe { &*self.ptr.as_ptr().add(idx) }
}
```

A `// SAFETY:` that just restates the code ("// SAFETY: deref the pointer") is worthless. It must say *what invariant makes this sound* and *who/what established it*.

> Edition 2024: an `unsafe fn` body is **no longer** an implicit `unsafe` block. You must write `unsafe { }` inside it (lint `unsafe_op_in_unsafe_fn` is now warn-by-default). This is good — it forces a SAFETY comment on each operation instead of one blanket "the whole fn is unsafe."

## `MaybeUninit`, never `mem::uninitialized`

`mem::uninitialized` is deprecated, and `mem::zeroed` is only sound for types where the all-zero bit pattern is valid. For uninitialized storage, use `MaybeUninit<T>`:

```rust
use std::mem::MaybeUninit;

let mut buf: [MaybeUninit<u8>; 1024] = [const { MaybeUninit::uninit() }; 1024];
let n = fill_from_somewhere(&mut buf);
// SAFETY: fill_from_somewhere initialized the first `n` bytes.
let init: &[u8] = unsafe {
    std::slice::from_raw_parts(buf.as_ptr() as *const u8, n)
};
```

Key rules: don't read a `MaybeUninit` before writing it; `assume_init` only after every byte you'll read is initialized; reading even one uninit byte as a real value is UB. For element-wise init prefer `MaybeUninit::write`, then either `assume_init_ref` per element or a carefully justified `slice::from_raw_parts` once you've tracked how many elements are initialized.

## Pointers: `NonNull`, `ptr::add`, provenance

- Use `NonNull<T>` over `*mut T` for "never-null, owned-ish" pointers: it's covariant and enables the null-pointer niche (so `Option<NonNull<T>>` is pointer-sized). Most collection internals use it. Since 1.99 std round-trips owned buffers through `NonNull` directly: `Vec::into_parts()` → `(NonNull<T>, len, cap)` / `unsafe { Vec::from_parts(ptr, len, cap) }`, and `Box::into_non_null(b)` / `unsafe { Box::from_non_null(p) }` — prefer these to `into_raw_parts`/`Box::into_raw` + `NonNull::new_unchecked`.
- **Custom allocators are stable since 1.100.** Implement `unsafe trait std::alloc::Allocator` (`allocate` / `deallocate` over `Layout` → `NonNull<[u8]>`) and pass it with `Vec::new_in(a)` / `Box::new_in(v, a)`; `Global` and `System` are the built-in ones, and `&A` is itself an allocator, so an arena is usually shared as `&arena`. The allocator is part of the type (`Vec<T, A>`), and the `_in` / `_with_allocator` variants of the raw-parts functions (`Vec::from_parts_in`, `Box::into_non_null_with_allocator`) must be used to round-trip such a collection — rebuilding it with the plain `from_parts` would free it with the wrong allocator (UB).
- `ptr.add(n)` / `ptr.offset(n)` advance by `n` **elements** (not bytes) and require the result to stay in-bounds of the *same allocation*; `wrapping_add` is the non-UB-but-useless-for-deref variant. For byte offsets use `byte_add`.
- Don't fabricate a pointer from an integer cast and deref it (provenance is lost → UB under Stacked/Tree Borrows). If you must, use `ptr::with_exposed_provenance` / `expose_provenance` and understand what they mean.
- Creating a `&T`/`&mut T` from a raw pointer asserts *full* reference validity (aligned, non-null, points to initialized `T`, no aliasing `&mut`). That's where most "it ran fine but miri screams" bugs live.

## `static mut` is effectively banned — use `Atomic*` / `Mutex` / `OnceLock`

In edition 2024, **taking a reference to `static mut` is a hard error** (`static_mut_refs`), because two `&mut` to the same static is trivial UB. Replace it:

| Need | Use |
|---|---|
| A counter / flag / single integer | `static C: AtomicU64 = AtomicU64::new(0);` |
| Lazily-initialized immutable global | `static X: OnceLock<T> = OnceLock::new();` then `X.get_or_init(...)` |
| Mutable shared state | `static S: Mutex<T> = Mutex::new(...);` (const-constructible since 1.63) |
| Compile-time constant | `const`, or `static` (immutable) |

```rust
use std::sync::atomic::{AtomicU64, Ordering};
static REQUESTS: AtomicU64 = AtomicU64::new(0);
REQUESTS.fetch_add(1, Ordering::Relaxed); // no unsafe, no UB
```

If you genuinely need raw `static mut` (rare, e.g. a single-threaded embedded global), use `&raw mut`/`&raw const` to get a pointer *without* forming a reference — but reconsider first. The same applies generally: `&mut x as *mut _` / `&x as *const _` first creates a reference (with all its aliasing guarantees) and then decays it; write `&raw mut x` / `&raw const x` instead. 1.99 adds an allow-by-default `raw_borrows_via_references` lint that flags exactly this — worth `#![warn(...)]` in unsafe-heavy crates.

**Unions in patterns (1.99 Reference change):** don't match a `union` field and another value in **one** pattern (the classic hand-rolled tagged union `match (tag, u) { (Tag::A, U { a }) => .. }`). The compiler may read the union field before checking the tag → UB. Match the tag first, then read the field in that arm (`unsafe { u.a }`).

## `transmute`: last resort, with preconditions

`transmute<A, B>` reinterprets bits and requires `size_of::<A>() == size_of::<B>()`. Before reaching for it, know that it's UB if the bits aren't a valid `B`, and that layout of non-`#[repr(C)]`/`#[repr(transparent)]` types is **unspecified** (no field-order guarantee, even between two identical-looking structs). Prefer the targeted alternative:

| Want | Use instead of `transmute` |
|---|---|
| Reinterpret int ↔ float bits | `f64::from_bits` / `to_bits` |
| Bytes ↔ POD struct | `bytemuck` crate (`Pod`/`Zeroable`, no `unsafe` at call site) — verify API via Context7 |
| `&[u8]` ↔ `&[T]` | `slice::align_to` (handles alignment prefix/suffix) or `bytemuck` |
| Change a lifetime | **Don't.** Fix the lifetime; transmuting lifetimes is how you get use-after-free |
| Pointer ↔ usize | `ptr as usize` / `with_exposed_provenance` |

If you do transmute, both types should be `#[repr(C)]` or `#[repr(transparent)]` so the layout is actually defined.

## Encapsulate: the safe-abstraction boundary

The point of `unsafe` is to build a **safe public API** whose internal invariants make the unsafety sound — `Vec`, `String`, `Arc` are all `unsafe` inside, safe outside. The boundary holds only if no safe caller can break the invariant:

```rust
pub struct Ascii(Vec<u8>); // invariant: always valid ASCII

impl Ascii {
    pub fn new(bytes: Vec<u8>) -> Option<Self> {
        bytes.is_ascii().then_some(Ascii(bytes)) // checked at the one entry point
    }
    pub fn as_str(&self) -> &str {
        // SAFETY: the only constructor rejects non-ASCII, and the field is
        // private, so the bytes are always valid UTF-8.
        unsafe { std::str::from_utf8_unchecked(&self.0) }
    }
}
```

Anti-pattern: a `pub` field (or a `pub fn` returning `&mut` to the inner data) that lets safe code break the invariant the `unsafe` relied on. Then a *safe* caller can trigger UB — and that means your function should have been `unsafe`, or your design is wrong.

## FFI rules that bite

- **`#[repr(C)]` on every type crossing the boundary.** The default Rust `repr` reorders fields; a plain `struct` shared with C is UB waiting to happen.
- **`extern "C"`** for the ABI. Edition 2024 wants `unsafe extern "C" { ... }` for blocks declaring foreign fns (the block keyword `unsafe` marks "I vouch these signatures match"); individual `fn` calls still need an `unsafe` block.
- **Never let a panic unwind across an FFI boundary** — it's UB. Wrap Rust callbacks exposed to C in `std::panic::catch_unwind` and convert to an error code. (`extern "C"` aborts-on-unwind by default since Rust 1.81, which turns UB into a clean abort — still: don't rely on it for control flow.)
- **Strings:** Rust `&str`/`String` are length-prefixed and **not** NUL-terminated. Use `CString` to *send* an owned NUL-terminated string to C (keep it alive while C holds the pointer!), and `CStr::from_ptr` to *borrow* one coming back. A classic bug: `CString::new(s).unwrap().as_ptr()` — the `CString` is dropped at the end of the statement and C reads freed memory. Bind it to a variable first.

```rust
use std::ffi::{CString, CStr};
use std::os::raw::c_char;

unsafe extern "C" {
    fn puts(s: *const c_char) -> i32;
}

let c = CString::new("hello").unwrap(); // owns the buffer
// SAFETY: `c` outlives the call; puts() doesn't retain the pointer.
unsafe { puts(c.as_ptr()); }
// `c` dropped here — after the call, so the pointer was valid throughout.
```

## The UB checklist + how to actually catch it

UB means the compiler may assume it *never happens* and miscompile around it — "it worked in release" proves nothing. The common ones:

- Dereferencing dangling / unaligned / null pointers.
- Two `&mut` to the same data, or `&mut` aliasing a `&` (the core aliasing rule, enforced by Stacked/Tree Borrows).
- Reading uninitialized memory; producing an invalid value (`bool` ≠ 0/1, niche-invalid enum, `char` > 0x10FFFF, dangling `&`).
- Data races (unsynchronized access from multiple threads, one writing).
- Breaking a function's documented safety contract.
- Unwinding across `extern "C"`; integer/pointer math that overflows the rules above.

**Verification (do this for any `unsafe`):**

- `cargo +nightly miri test` — an interpreter that detects most pointer-aliasing, uninit-read, and provenance UB. The single highest-value tool for `unsafe` code. (Slow; run targeted tests.)
- `cargo +nightly careful test` (`cargo-careful`) — runs std/your code with extra debug assertions and uninit-memory poisoning; cheaper than miri, catches a different slice.
- ASan/TSan via `-Zsanitizer=address,thread` on nightly for FFI / threading bugs miri can't reach (real C code).
- Keep `cargo test` (debug) in the loop: debug builds keep overflow checks and many `debug_assert!`s that release strips.

Miri can't see *through* FFI (it doesn't run the C side), so a clean miri run on a binding doesn't clear the C code — it clears your Rust-side usage.

## `bindgen` vs `cbindgen` (don't mix them up)

| Tool | Direction | Generates | Use when |
|---|---|---|---|
| `bindgen` | C header → Rust | Rust `extern` decls + `#[repr(C)]` structs | You call a C library *from* Rust |
| `cbindgen` | Rust → C header | A `.h` for C consumers | You expose a Rust `cdylib`/`staticlib` *to* C/C++ |

Run them in `build.rs` (or commit generated output for reproducibility). For `bindgen`, prefer a hand-written safe wrapper over the raw `-sys` bindings — the generated API is all `unsafe` and ergonomically hostile; the idiom is a `foo-sys` crate (raw) + a `foo` crate (safe wrapper). Both tools' option surfaces move fast — verify current builder API via Context7 before writing `build.rs`.
