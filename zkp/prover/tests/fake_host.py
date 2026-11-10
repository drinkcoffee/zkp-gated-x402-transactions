"""Stands in for the Rust host, so these tests can run without Rust or a prover."""

import argparse
import json
import os
import sys

from eth_abi import encode

parser = argparse.ArgumentParser()
parser.add_argument("--system")
parser.add_argument("--output")
args = parser.parse_args()

# The inputs arrive on stdin. Record them so tests can check how they were sent.
inputs = json.load(sys.stdin)
NAMES = ["chainId", "nonce", "issuedAt", "paymentAmount"]
if set(inputs) != {"toBeCheckedAddress", *NAMES}:
    sys.exit(2)
if record := os.environ.get("FAKE_HOST_RECORD"):
    with open(record, "w") as file:
        json.dump({"argv": sys.argv[1:], "stdin": inputs}, file)

mode = os.environ.get("FAKE_HOST_MODE", "ok")
if mode == "rejected":
    print("error: the payment amount is greater than 10000 ($0.01)", file=sys.stderr)
    sys.exit(3)
if mode == "crash":
    sys.exit(101)
if mode == "no-output":
    sys.exit(0)

values = [int(inputs[name]) for name in NAMES]
if mode == "wrong-values":
    values[3] += 1
if mode == "wrong-nonce":
    values[1] += 1
public_values = encode(["uint256"] * 4, values)
if mode == "address-leaked":
    public_values = encode(["address"] + ["uint256"] * 4, [inputs["toBeCheckedAddress"], *values])
proof = {
    "vkey": "0x" + ("ab" * 32 if mode != "bad-vkey" else "ab"),
    "publicValues": "0x" + public_values.hex(),
    "proof": "0x" if mode == "mock" else "0x" + "cd" * 40,
    "proofSystem": args.system,
}
with open(args.output, "w") as file:
    json.dump(proof, file)
