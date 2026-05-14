//! Stage 2 xref fixture — exercises intra- and inter-file ``calls`` edges.
//!
//! ``main`` calls:
//!   - ``helper_sum``  → intra-file (same-file top-level fn)
//!   - ``helpers::add``→ inter-file (use binding into helpers.rs)
//!
//! ``run_pipeline`` is a private inter-file call site.

use crate::helpers::{add, multiply as mul};

pub mod helpers;

pub fn helper_sum(a: i32, b: i32) -> i32 {
    a + b
}

pub fn main_entry() -> i32 {
    let x = helper_sum(1, 2);          // intra-file call
    let y = add(3, 4);                  // inter-file via use
    let z = mul(5, 6);                  // inter-file via aliased use
    x + y + z
}

pub fn run_pipeline() -> i32 {
    add(7, 8)                           // inter-file again, different src chunk
}
