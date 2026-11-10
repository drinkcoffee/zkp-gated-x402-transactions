# GatedTransfer contract

A [Foundry](https://getfoundry.sh) project with `GatedTransfer`, a contract that transfers USDC from a treasury to an x402 client, but only with a valid zero knowledge proof from the circuit in [`../zkp`](../zkp).

## How it works

The proof's public values are the chain ID, a nonce, the Unix time the proof was issued (`issuedAt`), and the payment amount. The x402 client calls `gatedTransfer(nonce, issuedAt, paymentAmount, proofBytes)`. The contract:

1. Checks that the caller is the x402 client.
2. Checks that `issuedAt` is not in the future, and that the proof is no older than `proofValidity`. That is 10 minutes by default.
3. Checks that the nonce has not been used before, then marks it as used.
4. Asks Succinct's SP1 verifier to check the proof against the circuit's verification key (`programVKey`), with `abi.encode(block.chainid, nonce, issuedAt, paymentAmount)` as the public values. The verifier reverts if the proof is not valid. That happens if the proof is for another chain, or for different values, or if the circuit did not accept `paymentAmount`.
5. Transfers `paymentAmount` USDC from the treasury to the x402 client.

If any step fails, the whole call reverts, and the nonce stays unused.

The treasury must have approved the contract to spend its USDC. USDC has no operator role, so this is an ERC-20 allowance, set with `approve`.

| Setting | Set by | Changed by |
| --- | --- | --- |
| SP1 verifier | The implementation's constructor | An upgrade |
| USDC | The implementation's constructor | An upgrade |
| Owner | `initialize` | `transferOwnership`, then `acceptOwnership` by the new owner |
| Treasury | `initialize` | The owner, with `setTreasury` |
| x402 client | `initialize` | The owner, with `setX402Client` |
| Program verification key | `initialize` | The owner, with `setProgramVKey` |
| Time limit for proofs (`proofValidity`) | `initialize`, to 10 minutes | The owner, with `setProofValidity` |

The contract runs behind an ERC1967 proxy and upgrades with UUPS. Only the owner can upgrade it, with `upgradeToAndCall`. Settings are kept in [ERC-7201](https://eips.ethereum.org/EIPS/eip-7201) namespaced storage, so new versions can add fields without overwriting them. Always use the proxy's address, never the implementation's.

## Limits

- **A proof proves little on its own.** The list of allowed addresses and the $0.01 limit are public, so anyone can make a valid proof for any amount up to $0.01. Only allowing the x402 client to call `gatedTransfer` is the main protection.
- **Each proof works once, but new proofs are easy to make.** The nonce is a public value, so a proof cannot be reused, even with its bytes altered. A proof also expires after `proofValidity`, and only works on the chain it was made for. But anyone can make a new proof with a new nonce, so the x402 client can transfer up to $0.01 per call, as many times as it likes, up to the treasury's allowance. Keep the allowance small.
- **Proving time counts against the time limit.** `zkp-prove` sets `issuedAt` before proving starts. If proving and submitting take longer than `proofValidity`, the proof expires, and you need a new one.
- **Only Groth16 proofs work.** The deployment script uses Succinct's Groth16 gateway, which is also `zkp-prove`'s default. PLONK proofs need a contract deployed with the PLONK gateway, `0x3B6041173B80E77f038f3F2C0f9744f04837185e`.

## Setup

Install Foundry:

```sh
curl -L https://foundry.paradigm.xyz | bash
foundryup
```

Then install the dependencies, from this directory:

```sh
forge install foundry-rs/forge-std@v1.17.0 OpenZeppelin/openzeppelin-contracts@v5.4.0 OpenZeppelin/openzeppelin-contracts-upgradeable@v5.4.0
```

Copy `.env.example` to `.env` and set your values. Forge reads `.env` automatically.

## Tests

```sh
forge build
forge test
```

The tests in `test/GatedTransfer.t.sol` use a mock verifier and a mock USDC. The tests in `test/DeployFork.t.sol` run the deployment script against a copy of Base, and are skipped unless `BASE_RPC_URL` is set. They check the real USDC and verifier addresses, and that the real verifier rejects a fake proof. They read from Base, but send nothing to it.

## Deploying to Base

Deploying costs gas. The script refuses to run on any chain other than Base mainnet (chain ID 8453), and sets:

| Value | Address |
| --- | --- |
| SP1 verifier | `0x397A5f7f3dBd538f23DE225B51f532c34448dA9B`, Succinct's SP1VerifierGateway for Groth16 |
| USDC | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` |

The owner, treasury, x402 client and program verification key come from `OWNER`, `TREASURY`, `X402_CLIENT` and `PROGRAM_VKEY`. Get the verification key from the `arguments.programVKey` field that `zkp-prove` writes. It changes whenever the circuit does.

Store the deployer's private key in an encrypted Foundry keystore, rather than in `.env`:

```sh
cast wallet import deployer --interactive
```

Then deploy, and verify the source on Basescan:

```sh
forge script script/Deploy.s.sol --rpc-url base --account deployer --broadcast --verify
```

The script prints the proxy's address. The treasury then approves the proxy to spend its USDC. For example, to allow $1.00:

```sh
cast send 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913 "approve(address,uint256)" <proxy address> 1000000 --rpc-url base --account treasury
```

## Making a transfer

Generate a proof with `zkp-prove`, then, within the time limit, send the values in `gatedTransfer.arguments` from its output, from the x402 client's account:

```sh
cast send <proxy address> "gatedTransfer(uint256,uint256,uint256,bytes)" <nonce> <issuedAt> <paymentAmount> <proofBytes> --rpc-url base --account x402client
```
