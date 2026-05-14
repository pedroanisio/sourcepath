//! Module ``foo`` — lives at ``src/foo.rs`` (depth 1).
//!
//! ``super::bar::bar_fn`` reaches up to the crate root and back down
//! into the sibling module ``bar``.

use super::bar::bar_fn;

pub mod sub;

pub fn foo_fn() -> i32 {
    bar_fn() + 1
}
