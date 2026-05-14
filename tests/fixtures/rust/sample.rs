//! Fixture exercised by tests/verify_rust_ast.py.
//!
//! Covers Stage 1 deep-AST extraction: pub/private functions, async,
//! structs/enums/traits, impl blocks (both inherent and trait impls),
//! inline #[test], #[derive], #[inline], #[cfg], and nested modules.
//!
//! Keep this file small and stable — the verifier asserts exact item
//! shapes, so unannounced edits will break the test.

use std::collections::HashMap;

#[derive(Debug, Clone)]
pub struct Account {
    pub id: u64,
    balance: f64,
}

#[derive(Debug)]
pub enum AccountKind {
    Savings,
    Checking,
    Investment { yearly_rate: f64 },
}

pub trait Persistable {
    fn save(&self) -> Result<(), String>;
    fn load(id: u64) -> Result<Self, String>
    where
        Self: Sized;
}

impl Account {
    pub fn new(id: u64) -> Self {
        Account { id, balance: 0.0 }
    }

    pub async fn deposit(&mut self, amount: f64) -> f64 {
        self.balance += amount;
        self.balance
    }

    #[inline]
    fn internal_audit(&self) -> bool {
        self.balance >= 0.0
    }
}

impl Persistable for Account {
    fn save(&self) -> Result<(), String> {
        Ok(())
    }

    fn load(id: u64) -> Result<Self, String> {
        Ok(Account::new(id))
    }
}

pub async fn fetch_account(id: u64) -> Option<Account> {
    Some(Account::new(id))
}

#[test]
fn account_starts_at_zero() {
    let a = Account::new(1);
    assert_eq!(a.balance, 0.0);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn deposit_increases_balance() {
        let _registry: HashMap<u64, Account> = HashMap::new();
    }
}
