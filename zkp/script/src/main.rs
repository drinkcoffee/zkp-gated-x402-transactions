//! Generates a proof for some public values and writes it to a JSON file.
//!
//! The prover is chosen by the SP1_PROVER and NETWORK_PRIVATE_KEY environment variables.

use std::{path::PathBuf, process::ExitCode};

use alloy_primitives::{Address, U256};
use alloy_sol_types::SolType;
use clap::{Parser, ValueEnum};
use serde::Serialize;
use sp1_sdk::{
    blocking::{ProveRequest, Prover, ProverClient},
    include_elf, Elf, HashableKey, ProvingKey, SP1Stdin,
};
use zkp_lib::{check, PublicValues};

const ELF: Elf = include_elf!("zkp-program");

/// Exit code for public values that break a rule, so no proof can exist.
const EXIT_REJECTED: u8 = 3;

#[derive(Copy, Clone, Debug, ValueEnum)]
enum ProofSystem {
    Groth16,
    Plonk,
}

#[derive(Parser)]
#[command(about = "Generate a proof for some public values")]
struct Args {
    #[arg(long)]
    to_be_checked_address: Address,
    #[arg(long)]
    max_payment_amount: U256,
    #[arg(long)]
    payment_amount: U256,
    #[arg(long, value_enum, default_value = "groth16")]
    system: ProofSystem,
    #[arg(long)]
    output: PathBuf,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ProofOutput {
    vkey: String,
    public_values: String,
    proof: String,
    proof_system: String,
}

fn main() -> ExitCode {
    let args = Args::parse();
    let values = PublicValues {
        toBeCheckedAddress: args.to_be_checked_address,
        maxPaymentAmount: args.max_payment_amount,
        paymentAmount: args.payment_amount,
    };

    // Reject here first, so a bad request never reaches a prover that charges for its work.
    if let Err(violation) = check(&values) {
        eprintln!("error: {violation}");
        return ExitCode::from(EXIT_REJECTED);
    }

    let encoded = PublicValues::abi_encode(&values);
    let mut stdin = SP1Stdin::new();
    stdin.write_vec(encoded.clone());

    let client = ProverClient::from_env();
    client
        .execute(ELF, stdin.clone())
        .run()
        .expect("the program failed to execute");

    let pk = client.setup(ELF).expect("failed to set up the program");
    let request = client.prove(&pk, stdin);
    let proof = match args.system {
        ProofSystem::Groth16 => request.groth16().run(),
        ProofSystem::Plonk => request.plonk().run(),
    }
    .expect("failed to generate the proof");

    client
        .verify(&proof, pk.verifying_key(), None)
        .expect("the generated proof did not verify");
    assert_eq!(
        proof.public_values.as_slice(),
        encoded.as_slice(),
        "the proof commits to different public values"
    );

    let output = ProofOutput {
        vkey: pk.verifying_key().bytes32().to_string(),
        public_values: format!("0x{}", hex::encode(&encoded)),
        proof: format!("0x{}", hex::encode(proof.bytes())),
        proof_system: format!("{:?}", args.system).to_lowercase(),
    };
    let json = serde_json::to_string_pretty(&output).expect("failed to serialize the proof");
    std::fs::write(&args.output, json).expect("failed to write the proof");
    ExitCode::SUCCESS
}
