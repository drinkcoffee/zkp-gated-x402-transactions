import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from eth_abi import decode

from zkp_prover import cli

ADDRESS = "0x141544BF5bf25cECeaB4B804Fe38b27B698B5F07"
FAKE_HOST = Path(__file__).with_name("fake_host.py")
GROTH16_GATEWAY = "0x397A5f7f3dBd538f23DE225B51f532c34448dA9B"
PLONK_GATEWAY = "0x3B6041173B80E77f038f3F2C0f9744f04837185e"

ORIGINAL_HOST_COMMAND = cli.host_command
RPC_CALLS = []


class RpcHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        RPC_CALLS.append(request)
        if self.path == "/down":
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/reject":
            reply = {"id": 1, "error": {"code": 3, "message": "execution reverted: WrongProof"}}
        else:
            reply = {"id": 1, "result": "0x"}
        body = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def rpc_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), RpcHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Use the fake host, and keep away from any real .env file."""

    def fake_command(*args):
        return [sys.executable, str(FAKE_HOST), *ORIGINAL_HOST_COMMAND(*args)[7:]]

    monkeypatch.setattr(cli, "host_command", fake_command)
    monkeypatch.setattr(cli, "ZKP_DIR", tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BASE_RPC_URL", raising=False)
    RPC_CALLS.clear()


def run(rpc_url, *args, max_amount="10000", amount="500", path=""):
    return cli.main([ADDRESS, max_amount, amount, "--rpc-url", rpc_url + path, *args])


def written(tmp_path):
    return json.loads((tmp_path / "proof.json").read_text())


def test_writes_a_submission_ready_for_base(rpc_url, tmp_path):
    assert run(rpc_url) == 0
    result = written(tmp_path)
    assert result["readyForBase"] is True
    assert result["chainId"] == 8453
    assert result["publicValues"] == {
        "toBeCheckedAddress": ADDRESS,
        "maxPaymentAmount": "10000",
        "paymentAmount": "500",
    }
    assert result["proofSystem"] == "groth16"
    assert result["verifier"] == GROTH16_GATEWAY
    assert result["function"] == "verifyProof(bytes32,bytes,bytes)"


def test_calldata_is_the_verifier_call(rpc_url, tmp_path):
    run(rpc_url)
    result = written(tmp_path)
    calldata = bytes.fromhex(result["calldata"][2:])
    assert calldata[:4].hex() == "41493c60"
    vkey, public_values, proof = decode(["bytes32", "bytes", "bytes"], calldata[4:])
    assert vkey == bytes.fromhex("ab" * 32)
    assert "0x" + public_values.hex() == result["arguments"]["publicValues"]
    assert proof == bytes.fromhex("cd" * 40)


def test_public_values_are_three_abi_words(rpc_url, tmp_path):
    run(rpc_url)
    encoded = bytes.fromhex(written(tmp_path)["arguments"]["publicValues"][2:])
    assert len(encoded) == 96
    address, max_amount, amount = decode(["address", "uint256", "uint256"], encoded)
    assert address.lower() == ADDRESS.lower()
    assert (max_amount, amount) == (10000, 500)


def test_checks_the_proof_on_base(rpc_url, tmp_path):
    run(rpc_url)
    result = written(tmp_path)
    assert len(RPC_CALLS) == 1
    assert RPC_CALLS[0]["method"] == "eth_call"
    assert RPC_CALLS[0]["params"] == [
        {"to": GROTH16_GATEWAY, "data": result["calldata"]},
        "latest",
    ]


def test_plonk_uses_its_own_verifier(rpc_url, tmp_path):
    assert run(rpc_url, "--system", "plonk") == 0
    result = written(tmp_path)
    assert result["proofSystem"] == "plonk"
    assert result["verifier"] == PLONK_GATEWAY


def test_lowercase_address_is_accepted(rpc_url, tmp_path):
    assert cli.main([ADDRESS.lower(), "10000", "500", "--rpc-url", rpc_url]) == 0
    assert written(tmp_path)["publicValues"]["toBeCheckedAddress"] == ADDRESS


def test_custom_output_path(rpc_url, tmp_path):
    assert run(rpc_url, "--output", "custom.json") == 0
    assert (tmp_path / "custom.json").exists()


def test_rpc_url_from_environment(rpc_url, tmp_path, monkeypatch):
    monkeypatch.setenv("BASE_RPC_URL", rpc_url)
    assert cli.main([ADDRESS, "10000", "500"]) == 0
    assert len(RPC_CALLS) == 1


def test_rejected_values_write_nothing(rpc_url, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_HOST_MODE", "rejected")
    assert run(rpc_url) == 1
    assert "no proof exists" in capsys.readouterr().err
    assert not (tmp_path / "proof.json").exists()
    assert RPC_CALLS == []


@pytest.mark.parametrize(
    "mode, message",
    [
        ("crash", "exit code 101"),
        ("no-output", "wrote no valid proof"),
        ("wrong-values", "different public values"),
        ("bad-vkey", "invalid vkey"),
    ],
)
def test_host_failures(rpc_url, tmp_path, monkeypatch, capsys, mode, message):
    monkeypatch.setenv("FAKE_HOST_MODE", mode)
    assert run(rpc_url) == 1
    assert message in capsys.readouterr().err
    assert not (tmp_path / "proof.json").exists()


def test_mock_proof_is_not_ready_and_skips_base(rpc_url, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_HOST_MODE", "mock")
    assert run(rpc_url) == 0
    assert written(tmp_path)["readyForBase"] is False
    assert RPC_CALLS == []
    assert "cannot be verified on Base" in capsys.readouterr().err


@pytest.mark.parametrize(
    "path, message",
    [("/reject", "rejected the proof"), ("/down", "could not reach Base")],
)
def test_unverified_proof_is_kept_but_not_ready(rpc_url, tmp_path, capsys, path, message):
    assert run(rpc_url, path=path) == 1
    assert message in capsys.readouterr().err
    assert written(tmp_path)["readyForBase"] is False


def test_existing_output_is_not_replaced(rpc_url, tmp_path, capsys):
    (tmp_path / "proof.json").write_text("existing")
    assert run(rpc_url) == 1
    assert "already exists" in capsys.readouterr().err
    assert (tmp_path / "proof.json").read_text() == "existing"


def test_cargo_not_installed(rpc_url, monkeypatch, capsys):
    monkeypatch.setattr(cli, "host_command", lambda *args: ["no-such-command-zkp"])
    assert run(rpc_url) == 1
    assert "install Rust and SP1" in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [
        ["0x123", "10000", "500"],
        [ADDRESS, "abc", "500"],
        [ADDRESS, "10000", "1.5"],
        [ADDRESS, "10000", "-1"],
        [ADDRESS, "10000", str(2**256)],
        [ADDRESS, "10000", "9" * 5000],
        [ADDRESS, "10000"],
        [ADDRESS, "10000", "500", "--system", "stark"],
    ],
)
def test_invalid_arguments(args):
    with pytest.raises(SystemExit) as exc:
        cli.main(args)
    assert exc.value.code == 2


def test_host_command_matches_the_rust_arguments(tmp_path):
    output = tmp_path / "proof.json"
    command = ORIGINAL_HOST_COMMAND(ADDRESS, 10000, 500, "plonk", output)
    assert command == [
        "cargo", "run", "--release", "--quiet", "--bin", "zkp-host", "--",
        "--to-be-checked-address", ADDRESS,
        "--max-payment-amount", "10000",
        "--payment-amount", "500",
        "--system", "plonk",
        "--output", str(output),
    ]  # fmt: skip
