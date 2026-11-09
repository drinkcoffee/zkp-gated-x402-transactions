import base64
import io
import json
import os
import pty
import select
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from eth_account import Account
from eth_account.messages import encode_typed_data

from x402_agent.cli import main
from x402_agent.exact_evm import TRANSFER_WITH_AUTHORIZATION_TYPES
from x402_agent.pay import USDC_ADDRESS, format_usdc

PRIVATE_KEY = "0x" + "11" * 32  # Test only. This well-known key must never hold funds.
ACCOUNT = Account.from_key(PRIVATE_KEY).address
PAY_TO = "0x1111111111111111111111111111111111111111"
BALANCE_CALL_DATA = "0x70a08231" + ACCOUNT[2:].lower().rjust(64, "0")
BALANCE = 12_345_678  # 12.345678 USDC
PROMPT = "Do you approve this transaction? "
RESOURCE = {
    "url": "https://api.example.test/weather",
    "description": "Current weather",
    "serviceName": "Weather API",
}


def option(**overrides):
    return {
        "scheme": "exact",
        "network": "eip155:8453",
        "amount": "10000",
        "asset": USDC_ADDRESS,
        "payTo": PAY_TO,
        "maxTimeoutSeconds": 60,
        "extra": {"name": "USD Coin", "version": "2"},
        **overrides,
    }


def b64(value):
    return base64.b64encode(json.dumps(value).encode()).decode()


def required(*accepts, resource=RESOURCE, version=2, error=None):
    payment_required = {"x402Version": version, "resource": resource, "accepts": list(accepts)}
    if error:
        payment_required["error"] = error
    return (402, {"PAYMENT-REQUIRED": b64(payment_required)}, b"{}")


def settlement(**overrides):
    return {
        "success": True,
        "transaction": "0xabc",
        "network": "eip155:8453",
        "payer": ACCOUNT,
        **overrides,
    }


V1_BODY = {"x402Version": 1, "accepts": [option()]}
EIP3009_EXTRA = {"name": "USD Coin", "version": "2", "assetTransferMethod": "eip3009"}
PERMIT2_EXTRA = {"name": "USD Coin", "version": "2", "assetTransferMethod": "permit2"}
FAILED = settlement(success=False, errorReason="insufficient_funds", transaction="")
PENDING = settlement(success=False, errorReason="settlement_pending", transaction="0xdef")

PAID_OK = (200, {"PAYMENT-RESPONSE": b64(settlement())}, b"paid content")
# path: the response to a request with a payment, if it is not PAID_OK.
PAID_ROUTES = {
    "/paid-settle-failed": (402, {"PAYMENT-RESPONSE": b64(FAILED)}, b"{}"),
    "/paid-settle-pending": (402, {"PAYMENT-RESPONSE": b64(PENDING)}, b"{}"),
    "/paid-rejected": required(option(), error="invalid_exact_evm_payload_signature"),
    "/paid-settled-then-500": (500, {"PAYMENT-RESPONSE": b64(settlement())}, b"boom"),
    "/paid-500": (500, {}, b"boom"),
    "/paid-no-settlement": (200, {}, b"paid content"),
    "/paid-no-content": (200, {"PAYMENT-RESPONSE": b64(settlement())}, b""),
    "/paid-binary": (200, {"PAYMENT-RESPONSE": b64(settlement())}, b"\xff\xfe\x00"),
    "/paid-hostile": (200, {"PAYMENT-RESPONSE": b64(settlement())}, b"a\nb\x1b[2Jc"),
    "/paid-redirect": (302, {"Location": "/ok"}, b""),
}

# path: (status, headers, body)
ROUTES = {
    "/ok": required(option()),
    "/redirect-to-ok": (302, {"Location": "/ok"}, b""),
    **{path: required(option()) for path in [*PAID_ROUTES, "/paid-drop"]},
    "/explicit-eip3009": required(option(extra=EIP3009_EXTRA)),
    "/permit2-then-eip3009": required(
        option(extra=PERMIT2_EXTRA, amount="1"), option(amount="20000")
    ),
    "/only-permit2": required(option(extra=PERMIT2_EXTRA)),
    "/only-other-scheme": required(option(scheme="upto")),
    "/v1-header": required(option(), version=1),
    "/too-expensive": required(option(amount="99000000")),
    "/long-timeout": required(option(maxTimeoutSeconds=10**9)),
    "/lowercase-pay-to": required(option(payTo="0x" + "ab" * 20)),
    "/bad-pay-to": required(option(payTo="0x123")),
    "/bad-timeout": required(option(maxTimeoutSeconds="60")),
    "/no-extra": required({k: v for k, v in option().items() if k != "extra"}),
    "/multiple": required(
        option(network="eip155:84532", amount="1"),
        option(asset="0x2222222222222222222222222222222222222222", amount="2"),
        option(amount="2500000"),
    ),
    "/lowercase-asset": required(option(asset=USDC_ADDRESS.lower())),
    "/round-up": required(option(amount="1")),
    "/optional-missing": required(option(), resource={"url": RESOURCE["url"]}),
    "/no-resource-url": required(option(), resource={"description": "x"}),
    "/hostile": required(
        option(),
        resource={**RESOURCE, "description": "x\n\x1b[2JPayment: $0.00‮"},
    ),
    "/no-base": required(option(network="eip155:84532")),
    "/no-usdc": required(option(asset="0x2222222222222222222222222222222222222222")),
    "/bad-amount": required(option(amount="1.5")),
    "/huge-amount": required(option(amount="9" * 100)),
    "/free": (200, {}, b"hello"),
    "/v1-body": (402, {}, json.dumps(V1_BODY).encode()),
    "/bad-header": (402, {"PAYMENT-REQUIRED": "%%%"}, b"{}"),
}


# (method, content type, content) of each request to a route with no payment.
RECEIVED = []
# Each request to a route with a payment.
PAID = []


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, headers, body):
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond(self, content):
        content_type = self.headers.get("Content-Type")
        signature = self.headers.get("PAYMENT-SIGNATURE")
        if signature is None:
            RECEIVED.append((self.command, content_type, content))
            self._send(*ROUTES[self.path])
            return
        PAID.append(
            {
                "path": self.path,
                "method": self.command,
                "content_type": content_type,
                "content": content,
                "payload": json.loads(base64.b64decode(signature)),
            }
        )
        if self.path == "/paid-drop":
            self.close_connection = True
            return
        self._send(*PAID_ROUTES.get(self.path, PAID_OK))

    def do_GET(self):
        self._respond(None)

    def do_POST(self):
        content = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path in ROUTES:
            self._respond(content)
            return
        request = json.loads(content)
        if self.path == "/rpc-http-error":
            self._send(500, {}, b"down")
        elif self.path == "/rpc-error":
            error = {"code": -32000, "message": "rate limited"}
            self._send(200, {}, json.dumps({"id": 1, "error": error}).encode())
        else:
            call = request["params"][0]
            assert request["method"] == "eth_call"
            assert call == {"to": USDC_ADDRESS, "data": BALANCE_CALL_DATA}
            reply = {"id": 1, "result": hex(BALANCE)}
            self._send(200, {}, json.dumps(reply).encode())

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def base_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()


@pytest.fixture(autouse=True)
def env(base_url, monkeypatch):
    PAID.clear()
    monkeypatch.setenv("X402_ACCOUNT_ADDRESS", ACCOUNT)
    monkeypatch.setenv("X402_PRIVATE_KEY", PRIVATE_KEY)
    monkeypatch.setenv("BASE_RPC_URL", base_url + "/rpc")


@pytest.fixture
def run(base_url, capsys, monkeypatch):
    def _run(path, key="n", request_args=("G",), params=()):
        monkeypatch.setattr(sys, "stdin", io.StringIO(key))
        code = main(["pay", *request_args, base_url + path, *params])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


def expect_error(result, message):
    code, out, err = result
    assert code == 1
    assert out == ""
    assert message in err


def test_display_and_cancel(run):
    code, out, err = run("/ok")
    assert code == 0
    assert err == ""
    assert out == (
        "Service: Weather API\n"
        "URL: https://api.example.test/weather\n"
        "Description: Current weather\n"
        "Payment: $0.01\n"
        f"Pay to: {PAY_TO}\n"
        "\n"
        f"Your balance ({ACCOUNT}) is: USDC$ 12.34\n"
        "\n"
        f"{PROMPT}\n"
        "Payment request cancelled\n"
    )
    assert PAID == []


def signer_of(payment):
    """Recover the signer, as USDC's transferWithAuthorization does on chain."""
    accepted = payment["accepted"]
    authorization = payment["payload"]["authorization"]
    message = encode_typed_data(
        domain_data={
            "name": accepted["extra"]["name"],
            "version": accepted["extra"]["version"],
            "chainId": 8453,
            "verifyingContract": USDC_ADDRESS,
        },
        message_types=TRANSFER_WITH_AUTHORIZATION_TYPES,
        message_data={
            "from": authorization["from"],
            "to": authorization["to"],
            "value": int(authorization["value"]),
            "validAfter": int(authorization["validAfter"]),
            "validBefore": int(authorization["validBefore"]),
            "nonce": bytes.fromhex(authorization["nonce"][2:]),
        },
    )
    return Account.recover_message(message, signature=payment["payload"]["signature"])


@pytest.mark.parametrize("key", ["Y", "y"])
def test_approve_pays(run, key):
    code, out, err = run("/ok", key)
    assert code == 0
    assert err == ""
    assert out.endswith(
        f"{PROMPT}\n"
        "Submitting payment...\n"
        "Payment complete\n"
        "Transaction: 0xabc\n"
        "Status: 200 OK\n"
        "\n"
        "paid content\n"
    )


def test_payment_payload(run):
    before = int(time.time())
    run("/ok", "y")
    after = int(time.time())

    assert len(PAID) == 1
    payment = PAID[0]["payload"]
    assert payment["x402Version"] == 2
    assert payment["resource"] == RESOURCE
    assert payment["accepted"] == option()

    authorization = payment["payload"]["authorization"]
    assert authorization["from"] == ACCOUNT
    assert authorization["to"] == PAY_TO
    assert authorization["value"] == "10000"
    assert authorization["validAfter"] == "0"
    assert before + 60 <= int(authorization["validBefore"]) <= after + 60
    assert len(bytes.fromhex(authorization["nonce"][2:])) == 32
    assert signer_of(payment) == ACCOUNT


def test_each_payment_has_a_new_nonce(run):
    run("/ok", "y")
    run("/ok", "y")
    first, second = (p["payload"]["payload"]["authorization"]["nonce"] for p in PAID)
    assert first != second


def test_signature_covers_the_amount(run):
    run("/ok", "y")
    payment = PAID[0]["payload"]
    payment["payload"]["authorization"]["value"] = "99000000"
    assert signer_of(payment) != ACCOUNT


def test_paid_get_is_resent_as_get(run):
    run("/ok", "y")
    assert PAID[0]["method"] == "GET"
    assert PAID[0]["content_type"] is None


def test_paid_post_resends_the_same_content(run):
    params = '{"city":  "Zürich"}'
    code, _, _ = run("/ok", "y", request_args=("P",), params=(params,))
    assert code == 0
    assert PAID[0]["method"] == "POST"
    assert PAID[0]["content_type"] == "application/json"
    assert PAID[0]["content"] == params.encode()


def test_payment_goes_to_the_url_that_answered(run):
    assert run("/redirect-to-ok", "y")[0] == 0
    assert PAID[0]["path"] == "/ok"


def test_payment_is_not_sent_to_a_redirect_target(run):
    code, _, err = run("/paid-redirect", "y")
    assert code == 1
    assert "HTTP 302" in err
    assert len(PAID) == 1


@pytest.mark.parametrize(
    "path, messages",
    [
        ("/paid-settle-failed", ["HTTP 402", "insufficient_funds"]),
        ("/paid-settle-pending", ["settlement_pending", "0xdef", "on chain"]),
        ("/paid-rejected", ["HTTP 402", "invalid_exact_evm_payload_signature"]),
        ("/paid-settled-then-500", ["settled in transaction 0xabc", "HTTP 500"]),
        ("/paid-500", ["HTTP 500"]),
        ("/paid-drop", ["may still settle", "on chain"]),
    ],
)
def test_payment_failures(run, path, messages):
    code, out, err = run(path, "y")
    assert code == 1
    assert "Payment complete" not in out
    for message in messages:
        assert message in err
    assert len(PAID) == 1  # A failed payment is never retried automatically.


def test_success_without_settlement_is_not_called_a_payment(run):
    code, out, _ = run("/paid-no-settlement", "y")
    assert code == 0
    assert "Payment complete" not in out
    assert "did not report a settlement transaction" in out


def test_success_with_no_content(run):
    code, out, _ = run("/paid-no-content", "y")
    assert code == 0
    assert out.endswith("Status: 200 OK\n")


def test_binary_content_is_not_printed(run):
    assert "(binary content, 3 bytes)" in run("/paid-binary", "y")[1]


def test_content_cannot_send_escape_codes(run):
    out = run("/paid-hostile", "y")[1]
    assert "a\nb?[2Jc\n" in out
    assert "\x1b" not in out


def test_long_server_timeout_is_capped(run):
    run("/long-timeout", "y")
    valid_before = int(PAID[0]["payload"]["payload"]["authorization"]["validBefore"])
    assert valid_before <= time.time() + 3600


def test_explicit_eip3009_option(run):
    assert run("/explicit-eip3009", "y")[0] == 0
    assert signer_of(PAID[0]["payload"]) == ACCOUNT


def test_skips_options_it_cannot_pay(run):
    code, out, _ = run("/permit2-then-eip3009", "y")
    assert code == 0
    assert "Payment: $0.02\n" in out
    assert PAID[0]["payload"]["accepted"]["amount"] == "20000"


def test_lowercase_addresses_are_signed(run):
    assert run("/lowercase-asset", "y")[0] == 0
    assert run("/lowercase-pay-to", "y")[0] == 0
    assert signer_of(PAID[1]["payload"]) == ACCOUNT


@pytest.mark.parametrize(
    "path, env_name, value, message",
    [
        ("/ok", "X402_PRIVATE_KEY", None, "set X402_PRIVATE_KEY"),
        ("/ok", "X402_PRIVATE_KEY", "0xsecretbutinvalid", "not a valid private key"),
        ("/ok", "X402_PRIVATE_KEY", "0x" + "22" * 32, "is not the key for"),
        ("/too-expensive", "X402_PRIVATE_KEY", PRIVATE_KEY, "balance is too low"),
    ],
)
def test_nothing_is_sent_when_payment_cannot_be_made(
    run, monkeypatch, path, env_name, value, message
):
    if value is None:
        monkeypatch.delenv(env_name)
    else:
        monkeypatch.setenv(env_name, value)
    code, out, err = run(path, "y")
    assert code == 1
    assert message in err
    assert "secret" not in err
    assert "Submitting" not in out
    assert PAID == []


def test_private_key_is_not_needed_to_view_or_cancel(run, monkeypatch):
    monkeypatch.delenv("X402_PRIVATE_KEY")
    code, out, _ = run("/ok", "n")
    assert code == 0
    assert out.endswith("Payment request cancelled\n")


def test_private_key_is_never_printed(run):
    code, out, err = run("/ok", "y")
    assert PRIVATE_KEY[2:] not in out + err
    assert PRIVATE_KEY[2:] not in json.dumps(PAID[0]["payload"])


@pytest.mark.parametrize("key", ["n", "N", " ", "\n", "x", ""])
def test_other_keys_cancel(run, key):
    code, out, _ = run("/ok", key)
    assert code == 0
    assert out.endswith(f"{PROMPT}\nPayment request cancelled\n")


def test_selects_base_usdc_option(run):
    assert "Payment: $2.50\n" in run("/multiple")[1]


def test_asset_address_case_is_ignored(run):
    assert run("/lowercase-asset")[0] == 0


def test_tiny_payment_is_not_shown_as_free(run):
    assert "Payment: $0.01\n" in run("/round-up")[1]


def test_optional_resource_fields_missing(run):
    _, out, _ = run("/optional-missing")
    assert out.startswith(
        "Service: (not provided)\n"
        "URL: https://api.example.test/weather\n"
        "Description: (not provided)\n"
    )


def test_server_text_cannot_inject_lines_or_escape_codes(run):
    _, out, _ = run("/hostile")
    assert "Description: x?" in out
    assert "\x1b" not in out
    assert "‮" not in out
    assert out.count("Payment:") == 2  # Both on the sanitized and the real line.
    assert "\nPayment: $0.00" not in out


@pytest.mark.parametrize(
    "path, message",
    [
        ("/free", "expected HTTP 402 but received 200"),
        ("/v1-body", "response has no PAYMENT-REQUIRED header"),
        ("/bad-header", "could not be decoded"),
        ("/no-resource-url", "no resource URL"),
        ("/no-base", "no payment option for the Base blockchain"),
        ("/no-usdc", "no payment option in USDC"),
        ("/only-permit2", "exact scheme with EIP-3009"),
        ("/only-other-scheme", "exact scheme with EIP-3009"),
        ("/v1-header", "only x402 version 2"),
        ("/bad-pay-to", "invalid payTo"),
        ("/bad-timeout", "invalid maxTimeoutSeconds"),
        ("/no-extra", "token's name and version"),
        ("/bad-amount", "invalid amount"),
        ("/huge-amount", "invalid amount"),
    ],
)
def test_errors(run, path, message):
    expect_error(run(path), message)


def test_get_request(run):
    RECEIVED.clear()
    assert run("/ok")[0] == 0
    assert RECEIVED == [("GET", None, None)]


def test_post_with_params_sends_them_unchanged(run):
    RECEIVED.clear()
    params = '{"city":  "Zürich", "days": 3}'
    code, out, _ = run("/ok", request_args=("P",), params=(params,))
    assert code == 0
    assert "Payment: $0.01\n" in out
    assert RECEIVED == [("POST", "application/json", params.encode())]


def test_lowercase_get(run):
    RECEIVED.clear()
    assert run("/ok", request_args=("g",))[0] == 0
    assert RECEIVED == [("GET", None, None)]


def test_lowercase_post_with_params(run):
    RECEIVED.clear()
    assert run("/ok", request_args=("p",), params=("{}",))[0] == 0
    assert RECEIVED == [("POST", "application/json", b"{}")]


def test_post_without_params_has_no_content(run):
    RECEIVED.clear()
    assert run("/ok", request_args=("P",))[0] == 0
    assert RECEIVED == [("POST", None, b"")]


@pytest.mark.parametrize(
    "args, message",
    [
        (["pay"], "required"),
        (["pay", "G"], "required"),
        (["pay", "http://127.0.0.1:1/"], "invalid choice"),
        (["pay", "X", "http://127.0.0.1:1/"], "invalid choice"),
        (["pay", "G", "http://127.0.0.1:1/", "{}"], "only be used with type P"),
        (["pay", "g", "http://127.0.0.1:1/", "{}"], "only be used with type P"),
        (["pay", "P", "http://127.0.0.1:1/", "{bad"], "not valid JSON"),
        (["pay", "P", "http://127.0.0.1:1/", ""], "not valid JSON"),
    ],
)
def test_usage_errors_make_no_request(args, message, capsys):
    RECEIVED.clear()
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
    assert message in capsys.readouterr().err
    assert RECEIVED == []


def test_unreachable_url(capsys):
    assert main(["pay", "G", "http://127.0.0.1:1/"]) == 1
    assert "request to" in capsys.readouterr().err


def test_account_not_configured(run, monkeypatch):
    monkeypatch.delenv("X402_ACCOUNT_ADDRESS")
    expect_error(run("/ok"), "set X402_ACCOUNT_ADDRESS")


def test_settings_loaded_from_env_file(run, base_url, monkeypatch, tmp_path):
    monkeypatch.delenv("X402_ACCOUNT_ADDRESS")
    monkeypatch.delenv("BASE_RPC_URL")
    (tmp_path / ".env").write_text(
        f"X402_ACCOUNT_ADDRESS={ACCOUNT}\nBASE_RPC_URL={base_url}/rpc\n"
    )
    code, out, _ = run("/ok")
    assert code == 0
    assert f"Your balance ({ACCOUNT}) is: USDC$ 12.34\n" in out


def test_env_file_found_in_parent_directory(run, monkeypatch, tmp_path):
    monkeypatch.delenv("X402_ACCOUNT_ADDRESS")
    (tmp_path / ".env").write_text(f"X402_ACCOUNT_ADDRESS={ACCOUNT}\n")
    subdirectory = tmp_path / "subdirectory"
    subdirectory.mkdir()
    monkeypatch.chdir(subdirectory)
    assert run("/ok")[0] == 0


def test_environment_takes_precedence_over_env_file(run, tmp_path):
    (tmp_path / ".env").write_text("X402_ACCOUNT_ADDRESS=0x123\n")
    code, out, _ = run("/ok")
    assert code == 0
    assert f"({ACCOUNT})" in out


def test_account_invalid(run, monkeypatch):
    monkeypatch.setenv("X402_ACCOUNT_ADDRESS", "0x123")
    expect_error(run("/ok"), "not a valid address")


@pytest.mark.parametrize(
    "rpc_path, message",
    [("/rpc-http-error", "could not read USDC balance"), ("/rpc-error", "rate limited")],
)
def test_balance_failure(run, base_url, monkeypatch, rpc_path, message):
    monkeypatch.setenv("BASE_RPC_URL", base_url + rpc_path)
    expect_error(run("/ok"), message)


@pytest.mark.parametrize(
    "atomic, round_up, expected",
    [
        (0, False, "0.00"),
        (9_999, False, "0.00"),
        (9_999, True, "0.01"),
        (10_000, False, "0.01"),
        (10_000, True, "0.01"),
        (1_500_000, False, "1.50"),
        (999_995_000, True, "1000.00"),
        (10**30, False, "1000000000000000000000000.00"),
    ],
)
def test_format_usdc(atomic, round_up, expected):
    assert format_usdc(atomic, round_up) == expected


@pytest.mark.parametrize(
    "key, expected",
    [
        (b"y", "Payment complete"),
        (b"Y", "Payment complete"),
        (b"n", "Payment request cancelled"),
        (b"\x1b[A", "Payment request cancelled"),  # Up arrow.
        (b"\x03", "Payment request cancelled"),  # Ctrl-C.
    ],
)
def test_single_key_press_in_terminal(base_url, key, expected):
    """Run the real CLI on a terminal. The key is sent with no Enter after it."""
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, "-m", "x402_agent.cli", "pay", "G", base_url + "/ok"],
        stdin=slave,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    os.close(slave)
    try:
        output = b""
        deadline = time.monotonic() + 30
        while PROMPT.encode() not in output:
            assert time.monotonic() < deadline, f"no prompt: {output!r}"
            if select.select([process.stdout], [], [], 1)[0]:
                output += os.read(process.stdout.fileno(), 4096)
        # Give the process time to enter raw mode, which discards earlier input.
        time.sleep(0.3)
        os.write(master, key)
        rest, err = process.communicate(timeout=30)
    finally:
        process.kill()
        os.close(master)
    assert err == b""
    assert process.returncode == 0
    assert expected in rest.decode()
    assert len(PAID) == (1 if expected == "Payment complete" else 0)
