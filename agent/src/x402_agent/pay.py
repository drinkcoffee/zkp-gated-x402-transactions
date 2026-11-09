"""The pay command: show an x402 payment request and pay it if the user approves."""

import base64
import json
import os
import re
import sys
import termios
import tty
from typing import Any

import httpx
from eth_account import Account
from eth_account.signers.local import LocalAccount

from x402_agent.exact_evm import build_payment_payload
from x402_agent.x402 import TIMEOUT_SECONDS, decode_x402

BASE_NETWORK = "eip155:8453"
USDC_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
DEFAULT_BASE_RPC_URL = "https://mainnet.base.org"
ACCOUNT_ENV_VAR = "X402_ACCOUNT_ADDRESS"
PRIVATE_KEY_ENV_VAR = "X402_PRIVATE_KEY"
RPC_ENV_VAR = "BASE_RPC_URL"

BALANCE_OF_SELECTOR = "70a08231"
ATOMIC_UNITS_PER_CENT = 10_000  # USDC has six decimal places on Base.
MAX_UINT256 = 2**256 - 1
ADDRESS_PATTERN = re.compile(r"0x[0-9a-fA-F]{40}")
# The server settles on chain before it responds, which can take much longer than
# an ordinary request.
PAYMENT_TIMEOUT_SECONDS = 120.0


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


def _printable_content(content: bytes) -> str:
    """Make response content safe to print, keeping its line structure."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return f"(binary content, {len(content)} bytes)"
    return "".join(
        char if char.isprintable() or char in "\n\t" else "?" for char in text
    )


def _request_headers(content: bytes | None) -> dict[str, str]:
    return {} if content is None else {"Content-Type": "application/json"}


def _fetch_payment_required(
    method: str, url: str, content: bytes | None
) -> tuple[str, dict[str, Any]]:
    """Return the URL that answered, after any redirects, and its PaymentRequired."""
    response = httpx.request(
        method,
        url,
        content=content,
        headers=_request_headers(content),
        follow_redirects=True,
        timeout=TIMEOUT_SECONDS,
    )
    if response.status_code != 402:
        raise PaymentError(f"expected HTTP 402 but received {response.status_code}")

    x402 = decode_x402(response.status_code, response.headers, response.content)
    if x402 is None or x402["source"] != "header:PAYMENT-REQUIRED":
        reasons = (x402 or {}).get("errors") or []
        detail = reasons[0] if reasons else "response has no PAYMENT-REQUIRED header"
        raise PaymentError(f"no payment information: {detail}")
    return str(response.url), x402["payment_required"]


def _is_supported(option: dict[str, Any]) -> bool:
    extra = option.get("extra")
    method = extra.get("assetTransferMethod") if isinstance(extra, dict) else None
    return option.get("scheme") == "exact" and method in (None, "eip3009")


def _select_payment_option(payment_required: dict[str, Any]) -> dict[str, Any]:
    """Return the first Base USDC payment option that this client can pay."""
    if payment_required["x402Version"] != 2:
        raise PaymentError("only x402 version 2 payment requests are supported")

    base_options = [
        option
        for option in payment_required["accepts"]
        if isinstance(option, dict) and option.get("network") == BASE_NETWORK
    ]
    if not base_options:
        raise PaymentError(f"no payment option for the Base blockchain ({BASE_NETWORK})")

    usdc_options = [
        option
        for option in base_options
        # EVM addresses compare case-insensitively: case is only a checksum.
        if isinstance(option.get("asset"), str)
        and option["asset"].lower() == USDC_ADDRESS.lower()
    ]
    if not usdc_options:
        raise PaymentError(f"no payment option in USDC ({USDC_ADDRESS}) on Base")

    for option in usdc_options:
        if _is_supported(option):
            _validate_option(option)
            return option
    raise PaymentError("no USDC option uses the exact scheme with EIP-3009 transfers")


def _validate_option(option: dict[str, Any]) -> None:
    _parse_amount(option.get("amount"))

    pay_to = option.get("payTo")
    if not isinstance(pay_to, str) or not ADDRESS_PATTERN.fullmatch(pay_to):
        raise PaymentError("payment option has an invalid payTo address")

    timeout = option.get("maxTimeoutSeconds")
    if type(timeout) is not int or timeout <= 0:
        raise PaymentError("payment option has an invalid maxTimeoutSeconds")

    extra = option.get("extra")
    if (
        not isinstance(extra, dict)
        or not isinstance(extra.get("name"), str)
        or not isinstance(extra.get("version"), str)
    ):
        raise PaymentError("payment option does not give the token's name and version")


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


def _load_account(address: str) -> LocalAccount:
    private_key = os.environ.get(PRIVATE_KEY_ENV_VAR)
    if not private_key:
        raise PaymentError(f"set {PRIVATE_KEY_ENV_VAR} in .env to make payments")
    try:
        account = Account.from_key(private_key)
    except Exception:  # Library errors could quote the key, so never show them.
        raise PaymentError(f"{PRIVATE_KEY_ENV_VAR} is not a valid private key") from None
    if account.address.lower() != address.lower():
        raise PaymentError(
            f"{PRIVATE_KEY_ENV_VAR} is not the key for {ACCOUNT_ENV_VAR} ({address})"
        )
    return account


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


def _report_payment_result(response: httpx.Response) -> None:
    """Print the result of a paid request, or raise PaymentError if it failed."""
    x402 = decode_x402(response.status_code, response.headers, response.content) or {}
    settlement = x402.get("settlement")
    if not isinstance(settlement, dict):
        settlement = {}
    transaction = _printable(settlement.get("transaction"))
    succeeded = response.is_success

    if settlement.get("success") is False or (not settlement and not succeeded):
        reason = settlement.get("errorReason") or (
            x402.get("payment_required") or {}
        ).get("error")
        message = f"payment failed with HTTP {response.status_code}"
        if reason:
            message += f": {_printable(reason)}"
        if settlement.get("transaction"):
            message += f" (transaction {transaction}: check it on chain before retrying)"
        raise PaymentError(message)

    if not succeeded:
        raise PaymentError(
            f"payment settled in transaction {transaction}, but the server "
            f"returned HTTP {response.status_code}"
        )

    print("Payment complete" if settlement else "Request succeeded")
    if settlement:
        print(f"Transaction: {transaction}")
    else:
        print("The server did not report a settlement transaction")
    print(f"Status: {response.status_code} {_printable(response.reason_phrase)}")
    if response.content:
        print()
        print(_printable_content(response.content))


def _execute_payment(
    method: str,
    url: str,
    content: bytes | None,
    address: str,
    balance: int,
    option: dict[str, Any],
    resource: dict[str, Any],
) -> None:
    account = _load_account(address)
    if _parse_amount(option["amount"]) > balance:
        raise PaymentError("your USDC balance is too low for this payment")

    payload = build_payment_payload(account, option, resource)
    signature_header = base64.b64encode(json.dumps(payload).encode()).decode("ascii")

    print("Submitting payment...", flush=True)
    try:
        # Never follow a redirect here: the signed payment would go to the new
        # location, and anyone holding it can submit it.
        response = httpx.request(
            method,
            url,
            content=content,
            headers={**_request_headers(content), "PAYMENT-SIGNATURE": signature_header},
            follow_redirects=False,
            timeout=PAYMENT_TIMEOUT_SECONDS,
        )
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        raise PaymentError(
            f"no response after submitting the payment, so it may still settle: "
            f"check {address} on chain before retrying ({exc})"
        ) from exc
    _report_payment_result(response)


def pay(method: str, url: str, content: bytes | None = None) -> None:
    address = _get_account_address()
    payment_url, payment_required = _fetch_payment_required(method, url, content)
    resource = payment_required.get("resource")
    if not isinstance(resource, dict) or not resource.get("url"):
        raise PaymentError("payment information has no resource URL")
    option = _select_payment_option(payment_required)
    balance = get_usdc_balance(address)

    print(f"Service: {_printable(resource.get('serviceName'))}")
    print(f"URL: {_printable(resource.get('url'))}")
    print(f"Description: {_printable(resource.get('description'))}")
    # The cost rounds up and the balance rounds down, so neither looks more
    # favourable to the user than it really is.
    print(f"Payment: ${format_usdc(_parse_amount(option['amount']), round_up=True)}")
    print(f"Pay to: {option['payTo']}")
    print()
    print(f"Your balance ({address}) is: USDC$ {format_usdc(balance)}")
    print()
    print("Do you approve this transaction? ", end="", flush=True)

    key = read_key()
    print()
    if key in ("Y", "y"):
        _execute_payment(method, payment_url, content, address, balance, option, resource)
    else:
        print("Payment request cancelled")
