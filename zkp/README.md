# ZKP circuit

A [Succinct SP1](https://docs.succinct.xyz/docs/sp1/introduction) zero knowledge circuit, and a Python program that generates proofs ready to submit to Base.

## The proof

The circuit checks two inputs. The payment amount is the proof's only public value, ABI encoded as `uint256`. The to be checked address is private, so the proof reveals nothing about it.

| Input | Visibility | Rule |
| --- | --- | --- |
| To be checked address | Private | Must be in the list of addresses, and must not be `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` |
| Payment amount | Public | Must be less than or equal to 10000 |

Amounts are in USDC's atomic units, and USDC has six decimal places on Base, so 10000 is $0.01.

The list of addresses is:

- `0x141544BF5bf25cECeaB4B804Fe38b27B698B5F07`
- `0x738c05d470351daaca60a5897d7154b0a8e90751`

The list, the limit of 10000 and the rules are compiled into the circuit, in `lib/src/lib.rs`. Changing them changes the program's verification key (`programVKey`), so any contract that checks these proofs needs the new key.

## Layout

| Directory | Contents |
| --- | --- |
| `lib` | The public values and the rules |
| `program` | The circuit, which runs in the SP1 zkVM |
| `script` | The Rust host (`zkp-host`), which generates proofs |
| `prover` | The Python program (`zkp-prove`) |

## Setup

Install the Protocol Buffers compiler, which the SP1 SDK needs to build. On macOS:

```sh
brew install protobuf
```

Install Rust:

```sh
curl -fsSL https://sh.rustup.rs | sh
```

Install `sp1up`, which adds `~/.sp1/bin` to your shell profile (`~/.zshenv` on zsh):

```sh
curl -fsSL https://raw.githubusercontent.com/succinctlabs/sp1/main/sp1up/install | bash
```

> [!NOTE]
> The [SP1 install docs](https://docs.succinct.xyz/docs/sp1/getting-started/install) use `https://sp1up.succinct.xyz`. On 9 November 2026 that address returned a `DEPLOYMENT_NOT_FOUND` error page, which produces `command not found` errors when piped to `bash`. The command above downloads the installer from Succinct's GitHub repository instead.

Open a new terminal, or run `source ~/.zshenv`, then install the SP1 toolchain and check that it works:

```sh
sp1up
cargo prove --version
```

If `sp1up` fails with a GitHub rate limit error, set `GITHUB_TOKEN` to a GitHub token and run it again.

Then copy `.env.example` to `.env` and set your values.

| Variable | Purpose | Default |
| --- | --- | --- |
| `SP1_PROVER` | Where proofs are generated: `network`, `cpu` or `mock` | `cpu` |
| `NETWORK_PRIVATE_KEY` | Account that requests and pays for proofs on the Succinct Prover Network | None (required for `network`) |
| `BASE_RPC_URL` | Base JSON-RPC endpoint used to check proofs | `https://mainnet.base.org` |

Variables already set in the environment take precedence over `.env`. Proving with `network` costs money. Local Groth16 and PLONK proving with `cpu` needs Docker and at least 16GB of RAM.

## Generating a proof

```sh
cd prover
uv run zkp-prove <to be checked address> <payment amount>
```

For example:

```sh
uv run zkp-prove 0x141544BF5bf25cECeaB4B804Fe38b27B698B5F07 500
```

| Option | Purpose | Default |
| --- | --- | --- |
| `--system` | Proof system: `groth16` or `plonk` | `groth16` |
| `--output` | File to write the result to. An existing file is never replaced | `proof.json` |
| `--rpc-url` | Base JSON-RPC endpoint used to check the proof | `BASE_RPC_URL` |

Inputs that break a rule fail before any prover is used, and no file is written. The inputs are passed to the Rust host on stdin, and the private address is never written to the result. The address is still in your shell history, and the `network` prover receives it, because it runs the circuit. After generating a proof, the program asks the verifier on Base to check it, which is read-only and costs nothing.

## The result

```json
{
  "readyForBase": true,
  "chainId": 8453,
  "publicValues": {
    "paymentAmount": "500"
  },
  "proofSystem": "groth16",
  "verifier": "0x397A5f7f3dBd538f23DE225B51f532c34448dA9B",
  "function": "verifyProof(bytes32,bytes,bytes)",
  "arguments": {
    "programVKey": "0x...",
    "publicValues": "0x00000000000000000000000000000000000000000000000000000000000001f4",
    "proofBytes": "0x..."
  },
  "calldata": "0x41493c60..."
}
```

`verifier` is Succinct's `SP1VerifierGateway` on Base for the proof system, and `calldata` is the encoded call to it. A contract can pass `arguments` to the gateway's `verifyProof`, and must compare `programVKey` to the key it expects. Without that comparison, a proof from any program would pass.

`readyForBase` is `true` only if the verifier on Base accepted the proof. It is `false`, and the proof is still saved, for a mock proof, if the verifier rejected the proof, or if Base could not be reached. The exit code is 1 for the last two.

## Tests

```sh
cargo test --release                 # the rules, and the compiled circuit
cargo test --release -- --ignored    # real proofs generated locally, which is slower
cd prover && uv run pytest           # the Python program, using a stand-in for the Rust host
```
