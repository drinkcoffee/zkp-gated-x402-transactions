//! The zero knowledge circuit. If the public values break a rule, this program panics
//! and so no proof can be generated.

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
