//! The zero knowledge circuit. If the public values break a rule, this program panics
//! before it commits anything. A prover could still prove that failed run, but the proof
//! has a non-zero exit code and no public values, and SP1's verifiers reject it.

#![no_main]
sp1_zkvm::entrypoint!(main);

use alloy_sol_types::SolType;
use zkp_lib::{check, PublicValues};

pub fn main() {
    let input = sp1_zkvm::io::read_vec();
    let values = PublicValues::abi_decode(&input).expect("input is not ABI encoded");

    check(&values).expect("public values break a rule");

    sp1_zkvm::io::commit_slice(&PublicValues::abi_encode(&values));
}
