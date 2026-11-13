//! Uses the derive built from references/macros.md (see ../gen_derive.py) on real types.
use hello_derive::Hello;

trait Hello {
    fn hello(&self) -> &'static str;
}

#[derive(Hello)]
struct Plain;

// generics + where-clause: only correct because the skeleton uses `split_for_impl`
#[derive(Hello)]
struct Wrapper<'a, T>
where
    T: Clone,
{
    _inner: &'a T,
}

#[derive(Hello)]
enum Either {
    _A,
}

#[test]
fn derive_expands_for_structs_enums_and_generics() {
    assert_eq!(Plain.hello(), "Plain");
    assert_eq!(Wrapper { _inner: &1u8 }.hello(), "Wrapper");
    assert_eq!(Either::_A.hello(), "Either");
}
