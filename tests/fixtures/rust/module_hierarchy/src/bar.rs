//! Module ``bar`` — lives at ``src/bar.rs`` (depth 1).
//!
//! ``self::nested::nested_fn`` resolves to ``bar::nested::nested_fn``
//! (the child module of this file).

use self::nested::nested_fn;

pub mod nested;

pub fn bar_fn() -> i32 {
    nested_fn()
}
