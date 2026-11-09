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

from x402_agent.cli import main
from x402_agent.pay import USDC_ADDRESS, format_usdc

ACCOUNT = "0x209693Bc6afc0C5328bA36FaF03C514EF312287C"
BALANCE_CALL_DATA = "0x70a08231" + ACCOUNT[2:].lower().rjust(64, "0")
BALANCE = 12_345_678  # 12.345678 USDC
PROMPT = "Do you approve this transaction? "
RESOURCE = {
    "url": "https://api.example.test/weather",
    "description": "Current weather",
    "serviceName": "Weather API",
}


def option(network="eip155:8453", asset=USDC_ADDRESS, amount="10000"):
    return {
        "scheme": "exact",
        "network": network,
        "amount": amount,
        "asset": asset,
        "payTo": "0x1111111111111111111111111111111111111111",
        "maxTimeoutSeconds": 60,
        "extra": {"name": "USD Coin", "version": "2"},
    }


def required(*accepts, resource=RESOURCE):
    payment_required = {"x402Version": 2, "resource": resource, "accepts": list(accepts)}
    encoded = base64.b64encode(json.dumps(payment_required).encode()).decode()
    return (402, {"PAYMENT-REQUIRED": encoded}, b"{}")


V1_BODY = {"x402Version": 1, "accepts": [option()]}

# path: (status, headers, body)
ROUTES = {
    "/ok": required(option()),
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


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, headers, body):
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(*ROUTES[self.path])

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
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
    monkeypatch.setenv("X402_ACCOUNT_ADDRESS", ACCOUNT)
    monkeypatch.setenv("BASE_RPC_URL", base_url + "/rpc")


@pytest.fixture
def run(base_url, capsys, monkeypatch):
    def _run(path, key="n"):
        monkeypatch.setattr(sys, "stdin", io.StringIO(key))
        code = main(["pay", base_url + path])
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
        "\n"
        f"Your balance ({ACCOUNT}) is: USDC$ 12.34\n"
        "\n"
        f"{PROMPT}\n"
        "Payment request cancelled\n"
    )


@pytest.mark.parametrize("key", ["Y", "y"])
def test_approve(run, key):
    code, out, _ = run("/ok", key)
    assert code == 0
    assert out.endswith(f"{PROMPT}\nnot implemented yet\n")


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
        ("/bad-amount", "invalid amount"),
        ("/huge-amount", "invalid amount"),
    ],
)
def test_errors(run, path, message):
    expect_error(run(path), message)


def test_unreachable_url(capsys):
    assert main(["pay", "http://127.0.0.1:1/"]) == 1
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
        (b"y", "not implemented yet"),
        (b"Y", "not implemented yet"),
        (b"n", "Payment request cancelled"),
        (b"\x1b[A", "Payment request cancelled"),  # Up arrow.
        (b"\x03", "Payment request cancelled"),  # Ctrl-C.
    ],
)
def test_single_key_press_in_terminal(base_url, key, expected):
    """Run the real CLI on a terminal. The key is sent with no Enter after it."""
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, "-m", "x402_agent.cli", "pay", base_url + "/ok"],
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
    assert rest.decode() == f"\n{expected}\n"
