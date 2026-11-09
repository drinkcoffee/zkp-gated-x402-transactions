import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from x402_agent.cli import main

REQUIREMENT_V2 = {
    "scheme": "exact",
    "network": "eip155:84532",
    "amount": "10000",
    "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    "payTo": "0x209693Bc6afc0C5328bA36FaF03C514EF312287C",
    "maxTimeoutSeconds": 60,
    "extra": {"name": "USDC", "version": "2"},
}
PAYMENT_REQUIRED_V2 = {
    "x402Version": 2,
    "resource": {"url": "http://example.test/paid"},
    "accepts": [REQUIREMENT_V2],
}
PAYMENT_REQUIRED_V1 = {
    "x402Version": 1,
    "error": "X-PAYMENT header is required",
    "accepts": [
        {
            "scheme": "exact",
            "network": "base-sepolia",
            "maxAmountRequired": "10000",
            "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            "payTo": "0x209693Bc6afc0C5328bA36FaF03C514EF312287C",
            "resource": "http://example.test/paid",
            "description": "Paid resource",
            "maxTimeoutSeconds": 60,
        }
    ],
}
SETTLEMENT = {
    "success": True,
    "transaction": "0xabc",
    "network": "eip155:84532",
    "payer": "0xdef",
}


def b64(value: object) -> str:
    return base64.b64encode(json.dumps(value).encode()).decode()


# path: (status, headers, body)
ROUTES = {
    "/v2": (402, {"PAYMENT-REQUIRED": b64(PAYMENT_REQUIRED_V2)}, b"{}"),
    "/v1": (402, {}, json.dumps(PAYMENT_REQUIRED_V1).encode()),
    "/both": (
        402,
        {"PAYMENT-REQUIRED": b64(PAYMENT_REQUIRED_V2)},
        json.dumps(PAYMENT_REQUIRED_V1).encode(),
    ),
    "/bad-header-v1-body": (
        402,
        {"PAYMENT-REQUIRED": "%%%not-base64%%%"},
        json.dumps(PAYMENT_REQUIRED_V1).encode(),
    ),
    "/lowercase-unpadded": (
        402,
        {"payment-required": b64(PAYMENT_REQUIRED_V2).rstrip("=")},
        b"{}",
    ),
    "/not-x402": (402, {}, b'{"message": "pay up"}'),
    "/free": (200, {"Content-Type": "text/plain"}, b"hello"),
    "/settled": (200, {"PAYMENT-RESPONSE": b64(SETTLEMENT)}, b"paid content"),
    "/settled-v1": (200, {"X-PAYMENT-RESPONSE": b64(SETTLEMENT)}, b"paid content"),
    "/binary": (200, {}, b"\xff\xfe\x00"),
    "/redirect": (302, {"Location": "/v2"}, b""),
    "/error": (500, {}, b"boom"),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status, headers, body = ROUTES[self.path]
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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


@pytest.fixture
def run(base_url, capsys):
    def _run(path):
        assert main(["get", base_url + path]) == 0
        return json.loads(capsys.readouterr().out)

    return _run


def test_v2_header(run):
    result = run("/v2")
    assert result["http"]["status"] == 402
    assert result["http"]["reason"] == "Payment Required"
    assert result["x402"] == {
        "version": 2,
        "source": "header:PAYMENT-REQUIRED",
        "payment_required": PAYMENT_REQUIRED_V2,
        "settlement": None,
        "errors": [],
    }


def test_v1_body(run):
    x402 = run("/v1")["x402"]
    assert x402["version"] == 1
    assert x402["source"] == "body"
    assert x402["payment_required"] == PAYMENT_REQUIRED_V1


def test_header_takes_precedence_over_body(run):
    assert run("/both")["x402"]["payment_required"] == PAYMENT_REQUIRED_V2


def test_bad_header_reported_and_body_still_decoded(run):
    x402 = run("/bad-header-v1-body")["x402"]
    assert x402["payment_required"] == PAYMENT_REQUIRED_V1
    assert "could not be decoded" in x402["errors"][0]


def test_header_name_case_and_missing_padding(run):
    assert run("/lowercase-unpadded")["x402"]["payment_required"] == PAYMENT_REQUIRED_V2


def test_402_without_x402_data(run):
    x402 = run("/not-x402")["x402"]
    assert x402["payment_required"] is None
    assert x402["errors"] == ["402 response contains no x402 payment information"]


def test_non_402_has_no_x402(run):
    result = run("/free")
    assert result["http"]["status"] == 200
    assert result["http"]["body"] == "hello"
    assert result["http"]["headers"]["content-type"] == "text/plain"
    assert result["x402"] is None


@pytest.mark.parametrize("path", ["/settled", "/settled-v1"])
def test_settlement_headers(run, path):
    assert run(path)["x402"]["settlement"] == SETTLEMENT


def test_binary_body(run):
    http = run("/binary")["http"]
    assert "body" not in http
    assert base64.b64decode(http["body_base64"]) == b"\xff\xfe\x00"


def test_follows_redirects(run, base_url):
    result = run("/redirect")
    assert result["http"]["url"] == base_url + "/v2"
    assert result["x402"]["version"] == 2


def test_server_error_still_succeeds(run):
    result = run("/error")
    assert result["http"]["status"] == 500
    assert result["x402"] is None


@pytest.mark.parametrize("url", ["http://127.0.0.1:1/", "ftp://example.test/", "http://[bad"])
def test_request_failure(url, capsys):
    assert main(["get", url]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err


def test_missing_url_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        main(["get"])
    assert exc.value.code == 2
