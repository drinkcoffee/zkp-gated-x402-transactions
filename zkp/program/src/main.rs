//! The zero knowledge circuit. It checks its inputs, then commits only the payment
//! amount, so the to be checked address stays private.
//!
//! If the inputs break a rule, this program panics. A prover could still prove that
//! failed run, but the proof has a non-zero exit code, and SP1's verifiers reject it.

#![no_main]
sp1_zkvm::entrypoint!(main);

use alloy_sol_types::SolType;
use zkp_lib::{check, Inputs, PublicValues};

pub fn main() {
    let input = sp1_zkvm::io::read_vec();
    let inputs = Inputs::abi_decode(&input).expect("input is not ABI encoded");

    check(&inputs).expect("inputs break a rule");

    let public = PublicValues {
        paymentAmount: inputs.paymentAmount,
    };
    sp1_zkvm::io::commit_slice(&PublicValues::abi_encode(&public));
}
