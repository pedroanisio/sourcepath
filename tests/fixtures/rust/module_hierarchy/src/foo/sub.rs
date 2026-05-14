//! Module ``foo::sub`` — lives at ``src/foo/sub.rs`` (depth 2).
//!
//! ``super::foo_fn`` reaches up to the parent module ``foo``.

use super::foo_fn;

pub fn sub_fn() -> i32 {
    foo_fn() * 2
}
