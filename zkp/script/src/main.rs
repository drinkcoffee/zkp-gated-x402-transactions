//! Generates a proof for some inputs and writes it to a JSON file.
//!
//! The inputs are read from stdin as JSON, `{"toBeCheckedAddress": "0x...",
//! "paymentAmount": "500"}`, so the private address does not appear in the process list.
//!
//! The prover is chosen by the SP1_PROVER and NETWORK_PRIVATE_KEY environment variables.

use std::{io::Read, path::PathBuf, process::ExitCode};

use alloy_primitives::{Address, U256};
use alloy_sol_types::SolType;
use clap::{Parser, ValueEnum};
use serde::{Deserialize, Serialize};
use sp1_sdk::{
    blocking::{ProveRequest, Prover, ProverClient},
    include_elf, Elf, HashableKey, ProvingKey, SP1Stdin,
};
use zkp_lib::{check, Inputs, PublicValues};

const ELF: Elf = include_elf!("zkp-program");

/// Exit code for inputs that break a rule, so no proof can exist.
const EXIT_REJECTED: u8 = 3;
/// Exit code for stdin that is not valid inputs.
const EXIT_BAD_INPUT: u8 = 2;

#[derive(Copy, Clone, Debug, ValueEnum)]
enum ProofSystem {
    Groth16,
    Plonk,
}

#[derive(Parser)]
#[command(about = "Generate a proof for inputs read from stdin as JSON")]
struct Args {
    #[arg(long, value_enum, default_value = "groth16")]
    system: ProofSystem,
    #[arg(long)]
    output: PathBuf,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct InputJson {
    to_be_checked_address: String,
    payment_amount: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ProofOutput {
    vkey: String,
    public_values: String,
    proof: String,
    proof_system: String,
}

fn read_inputs() -> Result<Inputs, String> {
    let mut text = String::new();
    std::io::stdin()
        .read_to_string(&mut text)
        .map_err(|e| format!("could not read stdin: {e}"))?;
    let json: InputJson =
        serde_json::from_str(&text).map_err(|e| format!("stdin is not valid JSON: {e}"))?;
    let address: Address = json
        .to_be_checked_address
        .parse()
        .map_err(|_| "toBeCheckedAddress is not an address".to_string())?;
    let amount = U256::from_str_radix(&json.payment_amount, 10)
        .map_err(|_| "paymentAmount is not a uint256".to_string())?;
    Ok(Inputs {
        toBeCheckedAddress: address,
        paymentAmount: amount,
    })
}

fn main() -> ExitCode {
    let args = Args::parse();
    let inputs = match read_inputs() {
        Ok(inputs) => inputs,
        Err(message) => {
            eprintln!("error: {message}");
            return ExitCode::from(EXIT_BAD_INPUT);
        }
    };

    // Reject here first, so a bad request never reaches a prover that charges for its work.
    if let Err(violation) = check(&inputs) {
        eprintln!("error: {violation}");
        return ExitCode::from(EXIT_REJECTED);
    }

    let expected = PublicValues::abi_encode(&PublicValues {
        paymentAmount: inputs.paymentAmount,
    });
    let mut stdin = SP1Stdin::new();
    stdin.write_vec(Inputs::abi_encode(&inputs));

    let client = ProverClient::from_env();
    // A program that panics does not make `run` return an error, so check the exit code.
    let (_, report) = client
        .execute(ELF, stdin.clone())
        .run()
        .expect("the program failed to execute");
    if report.exit_code != 0 {
        eprintln!("error: the circuit rejected the inputs");
        return ExitCode::from(EXIT_REJECTED);
    }

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
        expected.as_slice(),
        "the proof commits to different public values"
    );

    let output = ProofOutput {
        vkey: pk.verifying_key().bytes32().to_string(),
        public_values: format!("0x{}", hex::encode(&expected)),
        proof: format!("0x{}", hex::encode(proof.bytes())),
        proof_system: format!("{:?}", args.system).to_lowercase(),
    };
    let json = serde_json::to_string_pretty(&output).expect("failed to serialize the proof");
    std::fs::write(&args.output, json).expect("failed to write the proof");
    ExitCode::SUCCESS
}
