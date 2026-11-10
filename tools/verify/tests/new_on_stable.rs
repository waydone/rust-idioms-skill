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
