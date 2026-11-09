//! The rules that the zero knowledge circuit enforces on its public values.

use alloy_primitives::{address, Address, U256};
use alloy_sol_types::sol;
use std::fmt;

sol! {
    /// The public values of the proof, ABI encoded so that Solidity can decode them.
    struct PublicValues {
        address toBeCheckedAddress;
        uint256 maxPaymentAmount;
        uint256 paymentAmount;
    }
}

/// The addresses that the to be checked address is allowed to be.
pub const ALLOWED_ADDRESSES: [Address; 2] = [
    address!("141544BF5bf25cECeaB4B804Fe38b27B698B5F07"),
    address!("738c05d470351daaca60a5897d7154b0a8e90751"),
];

/// The address that the to be checked address must never be.
pub const FORBIDDEN_ADDRESS: Address = address!("833589fCD6eDb6E08f4c7C32D4f71b54bdA02913");

/// The value that the max payment amount must equal: $0.01, in USDC's atomic units.
/// USDC has six decimal places on Base.
pub const REQUIRED_MAX_PAYMENT_AMOUNT: U256 = U256::from_limbs([10_000, 0, 0, 0]);

/// A rule that the public values break.
#[derive(Debug, PartialEq, Eq)]
pub enum Violation {
    ForbiddenAddress,
    AddressNotInList,
    WrongMaxPaymentAmount,
    PaymentAmountTooLarge,
}

impl fmt::Display for Violation {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::ForbiddenAddress => "the to be checked address is the forbidden address",
            Self::AddressNotInList => "the to be checked address is not in the list of addresses",
            Self::WrongMaxPaymentAmount => "the max payment amount does not equal 10000 ($0.01)",
            Self::PaymentAmountTooLarge => {
                "the payment amount is greater than the max payment amount"
            }
        })
    }
}

/// Check the public values against every rule.
pub fn check(values: &PublicValues) -> Result<(), Violation> {
    if values.toBeCheckedAddress == FORBIDDEN_ADDRESS {
        return Err(Violation::ForbiddenAddress);
    }
    if !ALLOWED_ADDRESSES.contains(&values.toBeCheckedAddress) {
        return Err(Violation::AddressNotInList);
    }
    if values.maxPaymentAmount != REQUIRED_MAX_PAYMENT_AMOUNT {
        return Err(Violation::WrongMaxPaymentAmount);
    }
    if values.paymentAmount > values.maxPaymentAmount {
        return Err(Violation::PaymentAmountTooLarge);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn values(address: Address, max: u64, amount: u64) -> PublicValues {
        PublicValues {
            toBeCheckedAddress: address,
            maxPaymentAmount: U256::from(max),
            paymentAmount: U256::from(amount),
        }
    }

    #[test]
    fn accepts_each_listed_address() {
        for address in ALLOWED_ADDRESSES {
            assert_eq!(check(&values(address, 10_000, 500)), Ok(()));
        }
    }

    #[test]
    fn accepts_payment_amount_limits() {
        assert_eq!(check(&values(ALLOWED_ADDRESSES[0], 10_000, 0)), Ok(()));
        assert_eq!(check(&values(ALLOWED_ADDRESSES[0], 10_000, 10_000)), Ok(()));
    }

    #[test]
    fn rejects_forbidden_address() {
        assert_eq!(
            check(&values(FORBIDDEN_ADDRESS, 10_000, 500)),
            Err(Violation::ForbiddenAddress)
        );
    }

    #[test]
    fn rejects_address_not_in_list() {
        assert_eq!(
            check(&values(Address::ZERO, 10_000, 500)),
            Err(Violation::AddressNotInList)
        );
    }

    #[test]
    fn rejects_wrong_max_payment_amount() {
        for max in [0, 1000, 9_999, 10_001] {
            assert_eq!(
                check(&values(ALLOWED_ADDRESSES[0], max, 0)),
                Err(Violation::WrongMaxPaymentAmount)
            );
        }
    }

    #[test]
    fn rejects_max_payment_amount_that_only_matches_in_the_low_bits() {
        let mut large = values(ALLOWED_ADDRESSES[0], 10_000, 0);
        large.maxPaymentAmount += U256::from(1) << 64;
        assert_eq!(check(&large), Err(Violation::WrongMaxPaymentAmount));
    }

    #[test]
    fn rejects_payment_amount_above_max() {
        assert_eq!(
            check(&values(ALLOWED_ADDRESSES[0], 10_000, 10_001)),
            Err(Violation::PaymentAmountTooLarge)
        );
    }
}
