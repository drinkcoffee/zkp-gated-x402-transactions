"""Generate a zero knowledge proof for some public values, ready to submit to Base.

The rules on the public values live only in the circuit. This program runs the Rust
host to generate the proof, then packages it as a call to Succinct's verifier on Base.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from eth_abi import decode, encode
from eth_utils import keccak, to_checksum_address

ZKP_DIR = Path(__file__).resolve().parents[3]

BASE_CHAIN_ID = 8453
DEFAULT_BASE_RPC_URL = "https://mainnet.base.org"
RPC_TIMEOUT_SECONDS = 30.0

# SP1VerifierGateway contracts on Base, from succinctlabs/sp1-contracts.
VERIFIER_GATEWAYS = {
    "groth16": "0x397A5f7f3dBd538f23DE225B51f532c34448dA9B",
    "plonk": "0x3B6041173B80E77f038f3F2C0f9744f04837185e",
}
VERIFY_FUNCTION = "verifyProof(bytes32,bytes,bytes)"

ADDRESS_PATTERN = re.compile(r"0x[0-9a-fA-F]{40}")
EXIT_REJECTED = 3  # The host's exit code for public values that break a rule.
MAX_UINT256 = 2**256 - 1


class ProofError(Exception):
    """A proof could not be produced, or is not valid."""


def _address(value: str) -> str:
    if not ADDRESS_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError("must be a 0x address with 40 hex digits")
    return to_checksum_address(value)


def _uint256(value: str) -> int:
    if not (value.isascii() and value.isdigit() and len(value) <= 78):
        raise argparse.ArgumentTypeError("must be a non-negative whole number")
    number = int(value)
    if number > MAX_UINT256:
        raise argparse.ArgumentTypeError("must fit in 256 bits")
    return number


def host_command(address: str, max_amount: int, amount: int, system: str, output: Path) -> list[str]:
    return [
        "cargo", "run", "--release", "--quiet", "--bin", "zkp-host", "--",
        "--to-be-checked-address", address,
        "--max-payment-amount", str(max_amount),
        "--payment-amount", str(amount),
        "--system", system,
        "--output", str(output),
    ]  # fmt: skip


def generate_proof(address: str, max_amount: int, amount: int, system: str) -> dict[str, Any]:
    """Run the Rust host and return the proof it wrote."""
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "proof.json"
        command = host_command(address, max_amount, amount, system, output)
        try:
            # Send the host's stdout to file descriptor 2, the process's stderr.
            result = subprocess.run(command, cwd=ZKP_DIR, stdout=2)
        except FileNotFoundError:
            raise ProofError(
                f"{command[0]} was not found: install Rust and SP1 (see zkp/README.md)"
            ) from None
        if result.returncode == EXIT_REJECTED:
            raise ProofError("the public values break a rule, so no proof exists for them")
        if result.returncode != 0:
            raise ProofError(f"the proof host failed with exit code {result.returncode}")
        try:
            return json.loads(output.read_text())
        except (OSError, ValueError) as exc:
            raise ProofError(f"the proof host wrote no valid proof: {exc}") from exc


def _hex_bytes(proof: dict[str, Any], field: str) -> bytes:
    value = proof.get(field)
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ProofError(f"the proof host wrote an invalid {field}")
    try:
        return bytes.fromhex(value[2:])
    except ValueError:
        raise ProofError(f"the proof host wrote an invalid {field}") from None


def build_submission(
    proof: dict[str, Any], address: str, max_amount: int, amount: int, system: str
) -> dict[str, Any]:
    """Package a proof as a call to the verifier, after checking what it proves."""
    vkey = _hex_bytes(proof, "vkey")
    public_values = _hex_bytes(proof, "publicValues")
    proof_bytes = _hex_bytes(proof, "proof")
    if len(vkey) != 32:
        raise ProofError("the proof host wrote an invalid vkey")

    expected = encode(["address", "uint256", "uint256"], [address, max_amount, amount])
    if public_values != expected:
        raise ProofError("the proof commits to different public values than were requested")

    selector = keccak(text=VERIFY_FUNCTION)[:4]
    arguments = encode(["bytes32", "bytes", "bytes"], [vkey, public_values, proof_bytes])
    decoded = decode(["address", "uint256", "uint256"], public_values)
    return {
        "readyForBase": False,
        "chainId": BASE_CHAIN_ID,
        "publicValues": {
            "toBeCheckedAddress": to_checksum_address(decoded[0]),
            # Strings, because JSON readers often lose precision on large numbers.
            "maxPaymentAmount": str(decoded[1]),
            "paymentAmount": str(decoded[2]),
        },
        "proofSystem": system,
        "verifier": VERIFIER_GATEWAYS[system],
        "function": VERIFY_FUNCTION,
        "arguments": {
            "programVKey": proof["vkey"],
            "publicValues": proof["publicValues"],
            "proofBytes": proof["proof"],
        },
        "calldata": "0x" + (selector + arguments).hex(),
    }


def verify_on_base(submission: dict[str, Any], rpc_url: str) -> None:
    """Ask the verifier on Base to check the proof. This is read-only and costs nothing."""
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": submission["verifier"], "data": submission["calldata"]}, "latest"],
    }
    try:
        response = httpx.post(rpc_url, json=request, timeout=RPC_TIMEOUT_SECONDS)
        response.raise_for_status()
        reply = response.json()
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        raise ProofError(f"could not reach Base to check the proof: {exc}") from exc
    if not isinstance(reply, dict) or "result" not in reply:
        detail = reply.get("error") if isinstance(reply, dict) else reply
        raise ProofError(f"the verifier on Base rejected the proof: {json.dumps(detail)}")


def _write(submission: dict[str, Any], output: Path) -> None:
    output.write_text(json.dumps(submission, indent=2) + "\n")
    print(f"Wrote {output}", file=sys.stderr)


def run(args: argparse.Namespace) -> None:
    if args.output.exists():
        # Proofs can cost money to generate, so never replace one by accident.
        raise ProofError(f"{args.output} already exists: move it or choose another --output")

    system = args.system
    address = args.to_be_checked_address
    proof = generate_proof(address, args.max_payment_amount, args.payment_amount, system)
    submission = build_submission(
        proof, address, args.max_payment_amount, args.payment_amount, system
    )

    if submission["arguments"]["proofBytes"] == "0x":
        _write(submission, args.output)
        print("Mock proof generated: it cannot be verified on Base", file=sys.stderr)
        return

    rpc_url = args.rpc_url or os.environ.get("BASE_RPC_URL") or DEFAULT_BASE_RPC_URL
    try:
        verify_on_base(submission, rpc_url)
    except ProofError:
        _write(submission, args.output)
        raise
    submission["readyForBase"] = True
    _write(submission, args.output)
    print("The verifier on Base accepted the proof", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zkp-prove", description=__doc__)
    parser.add_argument("to_be_checked_address", type=_address, help="the to be checked address")
    parser.add_argument("max_payment_amount", type=_uint256, help="the max payment amount")
    parser.add_argument("payment_amount", type=_uint256, help="the payment amount")
    parser.add_argument(
        "--system", choices=sorted(VERIFIER_GATEWAYS), default="groth16", help="proof system"
    )
    parser.add_argument(
        "--output", type=Path, default=Path("proof.json"), help="file to write the result to"
    )
    parser.add_argument("--rpc-url", help="Base JSON-RPC endpoint used to check the proof")
    args = parser.parse_args(argv)

    load_dotenv(ZKP_DIR / ".env")
    try:
        run(args)
    except ProofError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
