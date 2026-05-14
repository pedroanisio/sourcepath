//! Stage 3 fixture — Rust integration test whose filename gives the
//! path-based tests_edges heuristic no signal. The crate name
//! ``integration_aggregate`` doesn't match any source file in src/,
//! so the only way to derive a (test → subject) edge is by walking
//! the test's ``use`` statements.

use xref_fixture::helpers::{add, multiply};

#[test]
fn add_works() {
    assert_eq!(add(2, 3), 5);
}

#[test]
fn multiply_works() {
    assert_eq!(multiply(4, 5), 20);
}
