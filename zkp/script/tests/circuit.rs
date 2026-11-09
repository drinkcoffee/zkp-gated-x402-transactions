//! Runs the compiled circuit itself.

use alloy_primitives::{address, Address, U256};
use alloy_sol_types::SolType;
use sp1_sdk::{
    blocking::{CpuProver, MockProver, ProveRequest, Prover},
    include_elf, Elf, ProvingKey, SP1PublicValues, SP1Stdin,
};
use zkp_lib::{PublicValues, ALLOWED_ADDRESSES, FORBIDDEN_ADDRESS};

const ELF: Elf = include_elf!("zkp-program");

fn stdin_for(address: Address, max: u64, amount: u64) -> SP1Stdin {
    let values = PublicValues {
        toBeCheckedAddress: address,
        maxPaymentAmount: U256::from(max),
        paymentAmount: U256::from(amount),
    };
    let mut stdin = SP1Stdin::new();
    stdin.write_vec(PublicValues::abi_encode(&values));
    stdin
}

/// Execute the circuit with no proof, and return its exit code and committed values.
///
/// A program that panics does not make `run` return an error. It reports a non-zero
/// exit code, which verifiers reject.
fn execute_stdin(stdin: SP1Stdin) -> (u64, Vec<u8>) {
    let (output, report) = MockProver::new()
        .execute(ELF, stdin)
        .run()
        .expect("the executor failed");
    (report.exit_code, output.as_slice().to_vec())
}

fn execute(address: Address, max: u64, amount: u64) -> (u64, Vec<u8>) {
    execute_stdin(stdin_for(address, max, amount))
}

fn assert_rejected(result: (u64, Vec<u8>)) {
    let (exit_code, committed) = result;
    assert_ne!(exit_code, 0, "the circuit must fail");
    assert!(committed.is_empty(), "the circuit must commit nothing");
}

#[test]
fn commits_the_three_public_values() {
    for address in ALLOWED_ADDRESSES {
        let (exit_code, output) = execute(address, 10_000, 10_000);
        assert_eq!(exit_code, 0);
        assert_eq!(output.len(), 96);
        let decoded = PublicValues::abi_decode(&output).unwrap();
        assert_eq!(decoded.toBeCheckedAddress, address);
        assert_eq!(decoded.maxPaymentAmount, U256::from(10_000));
        assert_eq!(decoded.paymentAmount, U256::from(10_000));
    }
}

#[test]
fn accepts_a_payment_amount_of_zero() {
    assert_eq!(execute(ALLOWED_ADDRESSES[1], 10_000, 0).0, 0);
}

#[test]
fn fails_for_the_forbidden_address() {
    assert_rejected(execute(FORBIDDEN_ADDRESS, 10_000, 1));
}

#[test]
fn fails_for_an_address_not_in_the_list() {
    let unlisted = address!("0000000000000000000000000000000000000001");
    assert_rejected(execute(unlisted, 10_000, 1));
}

#[test]
fn fails_for_the_wrong_max_payment_amount() {
    for max in [0, 1000, 9_999, 10_001] {
        assert_rejected(execute(ALLOWED_ADDRESSES[0], max, 0));
    }
}

#[test]
fn fails_for_a_payment_amount_above_the_max() {
    assert_rejected(execute(ALLOWED_ADDRESSES[0], 10_000, 10_001));
}

#[test]
fn fails_for_input_that_is_not_abi_encoded() {
    let mut stdin = SP1Stdin::new();
    stdin.write_vec(vec![1, 2, 3]);
    assert_rejected(execute_stdin(stdin));
}

/// Generates real proofs on this machine, which takes a while, so run this with
/// `cargo test --release -- --ignored`.
#[test]
#[ignore]
fn real_proofs_only_verify_for_valid_public_values() {
    let client = CpuProver::new();
    let pk = client.setup(ELF).expect("setup failed");

    // A valid proof verifies.
    let mut proof = client
        .prove(&pk, stdin_for(ALLOWED_ADDRESSES[0], 10_000, 500))
        .core()
        .run()
        .expect("proving failed");
    client
        .verify(&proof, pk.verifying_key(), None)
        .expect("a valid proof must verify");

    // The proof does not verify for different public values.
    let forged = PublicValues {
        toBeCheckedAddress: FORBIDDEN_ADDRESS,
        maxPaymentAmount: U256::from(10_000),
        paymentAmount: U256::from(500),
    };
    proof.public_values = SP1PublicValues::from(PublicValues::abi_encode(&forged).as_slice());
    assert!(client.verify(&proof, pk.verifying_key(), None).is_err());

    // A proof of a failed run does not verify.
    let failed = client
        .prove(&pk, stdin_for(FORBIDDEN_ADDRESS, 10_000, 500))
        .core()
        .run()
        .expect("proving failed");
    assert!(client.verify(&failed, pk.verifying_key(), None).is_err());
}
