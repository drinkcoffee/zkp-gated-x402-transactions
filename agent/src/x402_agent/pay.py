"""The pay command: show an x402 payment request and ask the user to approve it."""

import json
import os
import re
import sys
import termios
import tty
from typing import Any

import httpx

from x402_agent.x402 import TIMEOUT_SECONDS, decode_x402

BASE_NETWORK = "eip155:8453"
USDC_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
DEFAULT_BASE_RPC_URL = "https://mainnet.base.org"
ACCOUNT_ENV_VAR = "X402_ACCOUNT_ADDRESS"
RPC_ENV_VAR = "BASE_RPC_URL"

BALANCE_OF_SELECTOR = "70a08231"
ATOMIC_UNITS_PER_CENT = 10_000  # USDC has six decimal places on Base.
MAX_UINT256 = 2**256 - 1
ADDRESS_PATTERN = re.compile(r"0x[0-9a-fA-F]{40}")


class PaymentError(Exception):
    """The payment request cannot proceed."""


def format_usdc(atomic: int, round_up: bool = False) -> str:
    """Format atomic USDC units as 0.00, using integers so no precision is lost."""
    cents, remainder = divmod(atomic, ATOMIC_UNITS_PER_CENT)
    if round_up and remainder:
        cents += 1
    return f"{cents // 100}.{cents % 100:02d}"


def _printable(value: Any) -> str:
    """Make a server-supplied value safe to show on one line of the approval screen.

    This stops a server hiding or faking text with newlines or terminal escape codes.
    """
    if not isinstance(value, str) or not value:
        return "(not provided)"
    return "".join(char if char.isprintable() else "?" for char in value)


def _fetch_payment_required(url: str) -> dict[str, Any]:
    response = httpx.get(url, follow_redirects=True, timeout=TIMEOUT_SECONDS)
    if response.status_code != 402:
        raise PaymentError(f"expected HTTP 402 but received {response.status_code}")

    x402 = decode_x402(response.status_code, response.headers, response.content)
    if x402 is None or x402["source"] != "header:PAYMENT-REQUIRED":
        reasons = (x402 or {}).get("errors") or []
        detail = reasons[0] if reasons else "response has no PAYMENT-REQUIRED header"
        raise PaymentError(f"no payment information: {detail}")
    return x402["payment_required"]


def _select_base_usdc_amount(payment_required: dict[str, Any]) -> int:
    """Return the amount, in atomic units, of the first Base USDC payment option."""
    base_options = [
        option
        for option in payment_required["accepts"]
        if isinstance(option, dict) and option.get("network") == BASE_NETWORK
    ]
    if not base_options:
        raise PaymentError(f"no payment option for the Base blockchain ({BASE_NETWORK})")

    for option in base_options:
        asset = option.get("asset")
        # EVM addresses compare case-insensitively: case is only a checksum.
        if isinstance(asset, str) and asset.lower() == USDC_ADDRESS.lower():
            return _parse_amount(option.get("amount"))
    raise PaymentError(f"no payment option in USDC ({USDC_ADDRESS}) on Base")


def _parse_amount(amount: Any) -> int:
    if not (isinstance(amount, str) and amount.isascii() and amount.isdigit()):
        raise PaymentError("payment option has an invalid amount")
    try:
        value = int(amount)
    except ValueError:  # Python refuses to parse extremely long digit strings.
        raise PaymentError("payment option has an invalid amount") from None
    if value > MAX_UINT256:
        raise PaymentError("payment option has an invalid amount")
    return value


def _get_account_address() -> str:
    address = os.environ.get(ACCOUNT_ENV_VAR)
    if not address:
        raise PaymentError(f"set {ACCOUNT_ENV_VAR} to your account address in .env")
    if not ADDRESS_PATTERN.fullmatch(address):
        raise PaymentError(f"{ACCOUNT_ENV_VAR} is not a valid address")
    return address


def get_usdc_balance(address: str) -> int:
    """Return an account's USDC balance on Base, in atomic units."""
    rpc_url = os.environ.get(RPC_ENV_VAR) or DEFAULT_BASE_RPC_URL
    call_data = "0x" + BALANCE_OF_SELECTOR + address[2:].lower().rjust(64, "0")
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": USDC_ADDRESS, "data": call_data}, "latest"],
    }
    try:
        response = httpx.post(rpc_url, json=request, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        raise PaymentError(f"could not read USDC balance: {exc}") from exc

    if not isinstance(data, dict):
        raise PaymentError("could not read USDC balance: invalid RPC response")
    result = data.get("result")
    try:
        return int(result, 16)
    except (TypeError, ValueError):
        detail = json.dumps(data.get("error", "invalid RPC response"))
        raise PaymentError(f"could not read USDC balance: {detail}") from None


def read_key() -> str:
    """Read one key press without waiting for Enter. Returns "" at end of input."""
    if not sys.stdin.isatty():
        return sys.stdin.read(1)

    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    try:
        # Entering raw mode discards anything typed before the prompt appeared, so
        # an earlier key press cannot approve a payment the user has not yet seen.
        tty.setraw(fd)
        return os.read(fd, 1).decode("ascii", errors="replace")
    finally:
        # Also discards the rest of a multi-byte key, such as an arrow key.
        termios.tcsetattr(fd, termios.TCSAFLUSH, original)


def pay(url: str) -> None:
    address = _get_account_address()
    payment_required = _fetch_payment_required(url)
    resource = payment_required.get("resource")
    if not isinstance(resource, dict) or not resource.get("url"):
        raise PaymentError("payment information has no resource URL")
    amount = _select_base_usdc_amount(payment_required)
    balance = get_usdc_balance(address)

    print(f"Service: {_printable(resource.get('serviceName'))}")
    print(f"URL: {_printable(resource.get('url'))}")
    print(f"Description: {_printable(resource.get('description'))}")
    # The cost rounds up and the balance rounds down, so neither looks more
    # favourable to the user than it really is.
    print(f"Payment: ${format_usdc(amount, round_up=True)}")
    print()
    print(f"Your balance ({address}) is: USDC$ {format_usdc(balance)}")
    print()
    print("Do you approve this transaction? ", end="", flush=True)

    key = read_key()
    print()
    if key in ("Y", "y"):
        print("not implemented yet")
    else:
        print("Payment request cancelled")
