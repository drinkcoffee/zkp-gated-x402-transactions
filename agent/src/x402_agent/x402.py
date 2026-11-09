"""Decode x402 payment information from an HTTP response.

Specification: https://github.com/coinbase/x402/tree/main/specs
"""

import base64
import json
from collections.abc import Mapping
from typing import Any

TIMEOUT_SECONDS = 30.0
SETTLEMENT_HEADERS = ("PAYMENT-RESPONSE", "X-PAYMENT-RESPONSE")


def _decode_base64_json(value: str) -> Any:
    """Decode a base64 JSON header value, accepting URL-safe and unpadded base64."""
    normalized = value.strip().replace("-", "+").replace("_", "/")
    padded = normalized + "=" * (-len(normalized) % 4)
    return json.loads(base64.b64decode(padded, validate=True))


def _is_payment_required(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and "x402Version" in value
        and isinstance(value.get("accepts"), list)
    )


def decode_x402(
    status: int, headers: Mapping[str, str], body: bytes
) -> dict[str, Any] | None:
    """Return the x402 information in a response, or None if it has none.

    `headers` must look up names case-insensitively.
    """
    errors: list[str] = []
    payment_required = None
    source = None

    header = headers.get("PAYMENT-REQUIRED")
    if header is not None:
        try:
            decoded = _decode_base64_json(header)
        except ValueError as exc:
            errors.append(f"PAYMENT-REQUIRED header could not be decoded: {exc}")
        else:
            if _is_payment_required(decoded):
                payment_required = decoded
                source = "header:PAYMENT-REQUIRED"
            else:
                errors.append("PAYMENT-REQUIRED header is not a PaymentRequired object")

    if payment_required is None and status == 402:
        try:
            decoded = json.loads(body)
        except ValueError:
            decoded = None
        if _is_payment_required(decoded):
            payment_required = decoded
            source = "body"

    settlement = None
    for name in SETTLEMENT_HEADERS:
        value = headers.get(name)
        if value is None:
            continue
        try:
            settlement = _decode_base64_json(value)
        except ValueError as exc:
            errors.append(f"{name} header could not be decoded: {exc}")
        break

    if status == 402 and payment_required is None and settlement is None and not errors:
        errors.append("402 response contains no x402 payment information")

    if payment_required is None and settlement is None and not errors:
        return None

    version = None
    if payment_required is not None:
        version = payment_required["x402Version"]

    return {
        "version": version,
        "source": source,
        "payment_required": payment_required,
        "settlement": settlement,
        "errors": errors,
    }
