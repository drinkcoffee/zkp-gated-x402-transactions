//! Runs the compiled circuit itself. These tests generate no proofs and need no network.

use alloy_primitives::{address, Address, U256};
use alloy_sol_types::SolType;
use sp1_sdk::{
    blocking::{MockProver, Prover},
    include_elf, Elf, SP1Stdin,
};
use zkp_lib::{PublicValues, ALLOWED_ADDRESSES, FORBIDDEN_ADDRESS};

const ELF: Elf = include_elf!("zkp-program");

fn execute(address: Address, max: u64, amount: u64) -> Result<Vec<u8>, ()> {
    let values = PublicValues {
        toBeCheckedAddress: address,
        maxPaymentAmount: U256::from(max),
        paymentAmount: U256::from(amount),
    };
    let mut stdin = SP1Stdin::new();
    stdin.write_vec(PublicValues::abi_encode(&values));
    MockProver::new()
        .execute(ELF, stdin)
        .run()
        .map(|(output, _)| output.as_slice().to_vec())
        .map_err(|_| ())
}

#[test]
fn commits_the_three_public_values() {
    for address in ALLOWED_ADDRESSES {
        let output = execute(address, 10_000, 10_000).expect("valid values must execute");
        let decoded = PublicValues::abi_decode(&output).unwrap();
        assert_eq!(decoded.toBeCheckedAddress, address);
        assert_eq!(decoded.maxPaymentAmount, U256::from(10_000));
        assert_eq!(decoded.paymentAmount, U256::from(10_000));
    }
}

#[test]
fn fails_for_each_broken_rule() {
    let listed = ALLOWED_ADDRESSES[0];
    let unlisted = address!("0000000000000000000000000000000000000001");
    assert!(execute(FORBIDDEN_ADDRESS, 10_000, 1).is_err());
    assert!(execute(unlisted, 10_000, 1).is_err());
    assert!(execute(listed, 1000, 1).is_err());
    assert!(execute(listed, 10_000, 10_001).is_err());
}
