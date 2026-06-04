# Macros

Macros run at compile time and operate on **tokens** (or, for proc-macros, a parsed AST), not values. That power is also the cost: macro code is unreadable to a human skimming the call site, opaque to the type checker until expanded, and a tax on compile time and on every reader after you. The first idiom is therefore *don't*: reach for a function, generic, or trait first, and only escalate to a macro when the thing you need is genuinely beyond what the type system can express.

## When a macro is the right tool (and when it isn't)

| You want… | Use | Why not a macro |
|---|---|---|
| Same logic over many concrete types | **Generic `fn` + trait bound** | Type-checked, documented, IDE-navigable |
| One behavior behind a trait, many impls | **trait + blanket/derive** | Composes; macros don't |
| Avoid repeating a *function call* with different values | **function + closure / iterator** | Values, not tokens |
| Variadic args (`println!`-like), DSL syntax, compile-time codegen from a list | **`macro_rules!`** | Functions can't be variadic or take arbitrary syntax |
| Auto-impl a trait for `#[derive(...)]` types | **derive proc-macro** | `macro_rules!` can't read struct fields |
| Annotate/transform an item (`#[route(...)]`, `#[tokio::main]`) | **attribute proc-macro** | needs to rewrite the annotated item |
| An embedded DSL with custom parsing (`sqlx::query!`) | **function-like proc-macro** | needs `syn`-level parsing |

Rule of thumb: if you can phrase it as "I want to call the same code with different *values*", it's a function. Only when you need different *code* (or syntax that isn't valid Rust expression position) is it a macro.

## `macro_rules!` — fragment specifiers

A `macro_rules!` arm matches input against a pattern of *metavariables* `$name:fragment`. The fragment specifier decides **what syntax the matcher consumes** and **what follow-set is legal after it** — picking the wrong one is the #1 source of "no rules expected this token" errors.

| Specifier | Matches | Pick it when | Gotcha |
|---|---|---|---|
| `expr` | any expression | you'll *use* the value | parsed as one opaque unit — can't re-match inside; only `=> , ;` may follow |
| `ident` | a bare identifier | naming a fn/var/field you generate | not a path; `foo::bar` is not an `ident` |
| `ty` | a type | generating type-position code | follow-set restricted (`=> , ; | { [ : >` …) |
| `tt` | one token tree | forwarding/recursion, or "anything" | most permissive, least checked — overuse hides errors |
| `path` | `a::b::<T>` path | trait/type paths | can't be followed by `<` (ambiguous with generics) |
| `pat` | a pattern | `match`/`let` arms you build | edition-2024 `pat` includes top-level `|` (use `pat_param` to exclude) |
| `literal` / `lifetime` / `block` / `stmt` / `item` / `meta` / `vis` | as named | targeted matching | `vis` can match *empty* (no `pub`) |

**Why `expr`/`ty`/etc. can't be re-matched:** once a fragment is captured as a nonterminal it becomes a single opaque AST node ("`expr`-nonterminal"). You cannot then peek inside it with another macro arm. If you need to inspect structure, capture `$($t:tt)*` and recurse, or move to a proc-macro.

## Repetition `$()*` and the separator trap

```rust
macro_rules! vec_of {
    // $(...)* repeats; the `,` before * is the separator, * is "zero or more"
    ($($x:expr),* $(,)?) => {{          // trailing $(,)? makes a trailing comma optional
        let mut v = Vec::new();
        $( v.push($x); )*               // repetition count must match the capture
        v
    }};
}
let v = vec_of![1, 2, 3,];              // trailing comma OK
```

- Every `$( ... )*` in the body must use the **same** metavariable repetition depth as the matcher; mismatched depth is a compile error, not silent.
- `*` = 0+, `+` = 1+, `?` = 0 or 1 (no separator allowed with `?`).
- Wrap multi-statement bodies in **double braces** `{{ }}` so the expansion is one expression block, not a sequence the caller's context rejects.

## Hygiene — the part people get wrong

`macro_rules!` is **partially hygienic**: identifiers the macro *introduces* live in the macro's own syntax context, so they cannot capture or be captured by the caller's variables.

```rust
macro_rules! using_tmp {
    ($e:expr) => {{ let tmp = $e; tmp * 2 }};   // this `tmp` is hygienic
}
let tmp = 5;
let r = using_tmp!(tmp + 1);   // expands to `let tmp' = (tmp + 1); tmp' * 2` → no clash. r == 12
```

What hygiene does **not** cover:
- **Paths to items** (types, functions, consts) are *not* hygienic. `vec![]` expanding to `Vec::new()` only works if `Vec` resolves at the call site. Idiom: write absolute paths `$crate::...`, `::std::vec::Vec`, `::core::option::Option` in the expansion so the macro works regardless of the caller's imports.
- Identifiers **passed in** by the caller keep the caller's context — that's how you intentionally generate a binding the caller names.

`$crate` is the single most important hygiene tool for exported macros: it expands to the defining crate's root, so `$crate::Foo` resolves whether the user imported `Foo` or not.

## Exporting `macro_rules!` across crates

```rust
#[macro_export]                 // puts the macro at the *crate root*, regardless of module
macro_rules! my_macro { /* ... */ }
```

- `#[macro_export]` makes it `pub` from the crate root — users write `use yourcrate::my_macro;` (edition 2018+/2024 path-based import), not the old `#[macro_use] extern crate`.
- Inside the body, reference your own crate's items via **`$crate`**, never a bare `crate::` or assumed-imported name — the call site is in *another* crate.
- Within the *same* crate, a macro must be **defined textually before use** (or brought via `use`); ordering matters in a way normal items don't.

## Proc-macros — three kinds, one crate layout

Proc-macros are functions that take a `TokenStream` and return a `TokenStream`, compiled into a **separate crate** that runs inside the compiler.

```toml
# Cargo.toml of the proc-macro crate (edition 2024)
[package]
name = "my_derive"
edition = "2024"

[lib]
proc-macro = true               # this line is what makes it a proc-macro crate

[dependencies]
syn = { version = "2", features = ["full"] }   # "full" only if you parse fns/exprs/items
quote = "1"
proc-macro2 = "1"
# API surface of syn 2.x is large — verify non-trivial node types/fields with Context7
```

Why a separate crate: proc-macros are loaded as a compiler plugin (host arch), so they can't live next to normal target code. The conventional split is `my_crate` (re-exports + trait defs) depending on `my_crate_derive` (the `proc-macro = true` crate).

| Kind | Signature | Invoked as |
|---|---|---|
| **derive** | `#[proc_macro_derive(Name)] fn(TokenStream) -> TokenStream` | `#[derive(Name)]` |
| **attribute** | `#[proc_macro_attribute] fn(attr: TokenStream, item: TokenStream) -> TokenStream` | `#[name(args)] item` |
| **function-like** | `#[proc_macro] fn(TokenStream) -> TokenStream` | `name!(...)` |

### The three helper crates

- **`proc-macro2`** — a mirror of the compiler's `proc_macro::TokenStream` that works *outside* the compiler (so you can unit-test and use it in `build.rs`). `syn`/`quote` speak `proc_macro2::TokenStream`; you convert at the boundary with `.into()`.
- **`syn`** — parses tokens into a typed AST (`DeriveInput`, `ItemFn`, `Expr`, `Type`, …). Custom syntax: implement `syn::parse::Parse`.
- **`quote`** — the inverse: `quote! { ... }` builds a `TokenStream` with `#var` interpolation and `#(...)*` repetition (mirrors `macro_rules!` repetition).

### Derive skeleton (the idiomatic shape)

```rust
use proc_macro::TokenStream;
use quote::quote;
use syn::{parse_macro_input, DeriveInput, Error};

#[proc_macro_derive(Hello)]
pub fn derive_hello(input: TokenStream) -> TokenStream {
    let input = parse_macro_input!(input as DeriveInput);
    expand(input)
        .unwrap_or_else(Error::into_compile_error)   // errors become compile_error!, not panics
        .into()
}

fn expand(input: DeriveInput) -> syn::Result<proc_macro2::TokenStream> {
    let name = &input.ident;
    // split_for_impl handles generics + where-clauses correctly — don't hand-roll <T: ...>
    let (impl_g, ty_g, where_c) = input.generics.split_for_impl();
    Ok(quote! {
        impl #impl_g Hello for #name #ty_g #where_c {
            fn hello(&self) -> &'static str { stringify!(#name) }
        }
    })
}
```

(API confirmed against `syn` 2.x via Context7: `parse_macro_input!`, `DeriveInput`, `Generics::split_for_impl`, `Error::into_compile_error`. Verify field/variant walking — `Data`/`Fields`/`Index` — and any `full`-feature node before relying on it.)

## Error reporting — spanned, not panicked

The amateur move is `panic!("expected a struct")`: the user gets an error pointing at the macro invocation with no useful location. The idiom is `syn::Error` with a **span**, so the squiggle lands on the offending token:

```rust
if let syn::Data::Union(_) = &input.data {
    return Err(Error::new_spanned(&input.ident, "Hello cannot be derived for unions"));
}
```

`Error::new_spanned(tokens, msg)` attaches the span of `tokens`; `.combine(other)` accumulates several errors into one expansion. Use `quote_spanned!(field.span()=> ...)` when generated code might fail to type-check, so *that* error also points at the user's field, not your macro.

## `cargo expand` — the debugger for macros

You cannot reason about a macro by staring at it; expand it.

```bash
cargo install cargo-expand          # one-time; needs a nightly toolchain to run
cargo expand                        # whole crate, post-macro
cargo expand path::to::module       # narrow it down
cargo expand --test my_test         # expand a test target
```

For proc-macros specifically: a parse that "does nothing" usually means you returned the wrong token shape — `cargo expand` shows exactly what the compiler received. Pair with unit tests on `proc-macro2::TokenStream` (build input with `quote!`, assert on `.to_string()`), which run without the proc-macro harness.

## Anti-patterns

- **Macro where a generic fn would do.** If the body never needs different *syntax* — only different types or values — a generic function is type-checked, documented, and navigable. Macros opt out of all three.
- **Bare names in an exported `macro_rules!` body.** Writing `Vec::new()` / `Result` / `my_helper()` assumes the caller imported them. Use `$crate::...` and `::std::...` absolute paths.
- **`panic!`/`unwrap()` in a proc-macro.** Produces a useless "proc-macro panicked" with no source location. Return `syn::Result` and `Error::into_compile_error`.
- **`tt`-soup matchers.** Capturing everything as `$($t:tt)*` and re-parsing by hand throws away `macro_rules!`'s structural matching and degrades errors to "no rules expected token". Use the specific fragment specifier; escalate to a proc-macro when you truly need to inspect structure.
- **Recursion without a base case / past the limit.** Recursive `macro_rules!` hits `recursion_limit` (default 128). Either provide a terminating arm or `#![recursion_limit = "256"]` consciously — silent blow-ups look like hangs.
- **`syn` without pinning the feature set.** Pulling `features = ["full"]` when you only parse a `DeriveInput` bloats compile time; conversely, parsing an `ItemFn`/`Expr` without `full` fails to compile. Match the feature to what you parse.
- **Hand-rolling generics in `quote!`.** Writing `impl<T> Trait for #name<T>` drops bounds, lifetimes, const generics, and where-clauses. Always go through `generics.split_for_impl()`.

## Idiomatic vs merely-compiling

- A `macro_rules!` that compiles for *your* test module but breaks in a downstream crate (missing `$crate`/absolute paths) is a latent bug, not a working macro — test it from a separate crate or an integration test.
- A derive that `panic!`s on bad input compiles fine and behaves terribly; spanned `syn::Error` is the difference between a usable and a hostile macro.
- Reaching for a macro to "reduce boilerplate" that a trait + blanket impl would remove is the inverse of idiomatic: it adds the boilerplate of the macro machinery on top. Exhaust functions → generics → traits → `macro_rules!` → proc-macro, in that order.
