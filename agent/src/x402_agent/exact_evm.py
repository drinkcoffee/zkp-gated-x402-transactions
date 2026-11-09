"""Build x402 payment payloads for the `exact` scheme using EIP-3009.

Specification: https://github.com/coinbase/x402/blob/main/specs/schemes/exact/scheme_exact_evm.md
"""

import secrets
import time
from typing import Any

from eth_account.signers.local import LocalAccount
from eth_utils import to_checksum_address

# A server chooses maxTimeoutSeconds, so limit how long it can keep a signature usable.
MAX_AUTHORIZATION_SECONDS = 3600

TRANSFER_WITH_AUTHORIZATION_TYPES = {
    "TransferWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ]
}


def build_payment_payload(
    account: LocalAccount, option: dict[str, Any], resource: dict[str, Any]
) -> dict[str, Any]:
    """Sign a payment option and return the v2 PaymentPayload.

    The option's fields must already be validated.
    """
    value = int(option["amount"])
    valid_before = int(time.time()) + min(
        option["maxTimeoutSeconds"], MAX_AUTHORIZATION_SECONDS
    )
    nonce = secrets.token_bytes(32)

    signed = account.sign_typed_data(
        domain_data={
            "name": option["extra"]["name"],
            "version": option["extra"]["version"],
            "chainId": int(option["network"].split(":")[1]),
            "verifyingContract": to_checksum_address(option["asset"]),
        },
        message_types=TRANSFER_WITH_AUTHORIZATION_TYPES,
        message_data={
            "from": account.address,
            "to": to_checksum_address(option["payTo"]),
            "value": value,
            "validAfter": 0,
            "validBefore": valid_before,
            "nonce": nonce,
        },
    )

    return {
        "x402Version": 2,
        "resource": resource,
        "accepted": option,
        "payload": {
            "signature": signed.signature.to_0x_hex(),
            "authorization": {
                "from": account.address,
                "to": option["payTo"],
                "value": str(value),
                "validAfter": "0",
                "validBefore": str(valid_before),
                "nonce": "0x" + nonce.hex(),
            },
        },
    }
