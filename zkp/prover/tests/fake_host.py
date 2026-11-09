"""Stands in for the Rust host, so these tests can run without Rust or a prover."""

import argparse
import json
import os
import sys

from eth_abi import encode

parser = argparse.ArgumentParser()
parser.add_argument("--to-be-checked-address")
parser.add_argument("--max-payment-amount", type=int)
parser.add_argument("--payment-amount", type=int)
parser.add_argument("--system")
parser.add_argument("--output")
args = parser.parse_args()

mode = os.environ.get("FAKE_HOST_MODE", "ok")
if mode == "rejected":
    print("error: the max payment amount does not equal 10000 ($0.01)", file=sys.stderr)
    sys.exit(3)
if mode == "crash":
    sys.exit(101)
if mode == "no-output":
    sys.exit(0)

amount = args.payment_amount + 1 if mode == "wrong-values" else args.payment_amount
public_values = encode(
    ["address", "uint256", "uint256"],
    [args.to_be_checked_address, args.max_payment_amount, amount],
)
proof = {
    "vkey": "0x" + ("ab" * 32 if mode != "bad-vkey" else "ab"),
    "publicValues": "0x" + public_values.hex(),
    "proof": "0x" if mode == "mock" else "0x" + "cd" * 40,
    "proofSystem": args.system,
}
with open(args.output, "w") as file:
    json.dump(proof, file)
