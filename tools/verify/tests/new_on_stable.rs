//! The "new on stable since 1.95" APIs SKILL.md recommends — fails to compile on an older toolchain.
use std::{assert_matches, debug_assert_matches}; // 1.96: stable but NOT in the prelude

#[derive(Debug, PartialEq)]
enum ConfigError {
    Empty,
    Bad(String),
}

fn parse_config(s: &str) -> Result<u32, ConfigError> {
    (!s.is_empty()).ok_or(ConfigError::Empty)?; // 1.98: bool::ok_or
    s.parse().map_err(|_| ConfigError::Bad(s.into()))
}

#[test]
fn assert_matches_with_guards() {
    assert_matches!(parse_config(""), Err(ConfigError::Empty));
    assert_matches!(parse_config("x"), Err(ConfigError::Bad(s)) if s == "x");
    debug_assert_matches!(parse_config("7"), Ok(7));
}

#[test]
fn bool_ok_or_and_map_or_default() {
    let lazy: Result<(), &str> = true.ok_or_else(|| "never");
    assert_eq!(lazy, Ok(()));
    assert_eq!(Some("abc").map_or_default(|s| s.len()), 3); // 1.98
    assert_eq!(None::<&str>.map_or_default(|s| s.len()), 0);
    assert_eq!(Err::<u8, ()>(()).map_or_default(u32::from), 0);
}

#[test]
fn nonnull_raw_parts_round_trips() {
    let (ptr, len, cap) = vec![1u8, 2, 3].into_parts(); // 1.99
    let v = unsafe { Vec::from_parts(ptr, len, cap) };
    assert_eq!(v, [1, 2, 3]);
    let p = Box::into_non_null(Box::new(5i32)); // 1.99
    assert_eq!(*unsafe { Box::from_non_null(p) }, 5);
}

// ---- 1.100 ----

/// 1.100: `!` is a stable type; `Infallible` is an alias of it.
fn never_fails(s: &str) -> Result<usize, !> {
    Ok(s.len())
}

#[derive(Debug, PartialEq)]
struct AppError;

// There is NO blanket `impl<T> From<!> for T`: `never_fails(s)?` in a fn returning
// `Result<_, AppError>` is E0277 unless the error type opts in like this.
impl From<!> for AppError {
    fn from(never: !) -> Self {
        never
    }
}

fn uses_question_mark(s: &str) -> Result<usize, AppError> {
    Ok(never_fails(s)? + 1)
}

#[test]
fn never_type_is_stable() {
    let Ok(n) = never_fails("abc"); // irrefutable: the Err side is uninhabited
    assert_eq!(n, 3);
    assert_eq!(uses_question_mark("abc"), Ok(4));
    let via_alias: Result<u8, std::convert::Infallible> = Ok(1);
    let same: Result<u8, !> = via_alias; // same type, no conversion
    let Ok(v) = same;
    assert_eq!(v, 1);
    let parsed: Result<String, !> = "x".parse::<String>(); // FromStr for String has Err = Infallible = !
    let Ok(p) = parsed;
    assert_eq!(p, "x");
}

#[test]
fn small_std_additions_1_100() {
    let mut on = false;
    on.toggle();
    assert!(on);
    assert_eq!("v1.2.3".trim_prefix('v'), "1.2.3"); // strip_prefix(..).unwrap_or(s)
    assert_eq!("main.rs".trim_suffix(".rs"), "main");
    assert_eq!("main.rs".trim_suffix(".go"), "main.rs");
    assert_eq!(Vec::from_fn(4, |i| i * i), [0, 1, 4, 9]);
    let b: Box<String> = Box::map(Box::new(7u64), |n| n.to_string()); // associated fn, takes T by value
    assert_eq!(*b, "7");
    let a = std::sync::Arc::map(std::sync::Arc::new(7u32), |n| n + 1); // closure gets &T
    assert_eq!(*a, 8);
    let r = std::rc::Rc::map(std::rc::Rc::new(String::from("ab")), |s| s.len());
    assert_eq!(*r, 2);
    let (value, slot) = Box::take(Box::new(5u8));
    assert_eq!((value, *Box::write(slot, 6)), (5, 6));
}

#[test]
fn drop_guard_runs_or_is_dismissed() {
    use std::cell::Cell;
    use std::mem::DropGuard;
    let log = Cell::new(0);
    {
        let mut g = DropGuard::new(1, |v| log.set(log.get() + v));
        *g += 9; // Deref/DerefMut to the wrapped value
    }
    assert_eq!(log.get(), 10);
    let g = DropGuard::new(100, |v| log.set(log.get() + v));
    assert_eq!(DropGuard::dismiss(g), 100); // disarm, get the value back
    assert_eq!(log.get(), 10);
}

#[test]
fn allocator_api_is_stable() {
    use std::alloc::{Allocator, Global, System};
    let mut v = Vec::new_in(System);
    v.extend([1, 2, 3]);
    assert_eq!(v, [1, 2, 3]);
    let b = Box::new_in(5u8, System);
    assert_eq!(*b, 5);
    fn generic<A: Allocator>(a: A) -> Vec<u8, A> {
        Vec::with_capacity_in(8, a)
    }
    assert!(generic(Global).capacity() >= 8);
}
