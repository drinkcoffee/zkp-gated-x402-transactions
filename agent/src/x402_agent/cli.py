"""Command line entry point for the x402 agent."""

import argparse
import base64
import json
import sys
from collections.abc import Sequence
from typing import Any

import httpx
from dotenv import find_dotenv, load_dotenv

from x402_agent.pay import PaymentError, pay
from x402_agent.x402 import TIMEOUT_SECONDS, decode_x402


def get(url: str) -> dict[str, Any]:
    """GET a URL and return the HTTP response and its decoded x402 information."""
    response = httpx.get(url, follow_redirects=True, timeout=TIMEOUT_SECONDS)

    http: dict[str, Any] = {
        "url": str(response.url),
        "status": response.status_code,
        "reason": response.reason_phrase,
        "http_version": response.http_version,
        "headers": dict(response.headers),
    }
    try:
        http["body"] = response.content.decode("utf-8")
    except UnicodeDecodeError:
        http["body_base64"] = base64.b64encode(response.content).decode("ascii")

    return {
        "http": http,
        "x402": decode_x402(response.status_code, response.headers, response.content),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="x402-agent", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    get_parser = commands.add_parser(
        "get", help="GET a URL and show the HTTP response and x402 payment information"
    )
    get_parser.add_argument("url", help="URL to request")
    pay_parser = commands.add_parser(
        "pay", help="Show the payment a URL requires and ask for approval"
    )
    pay_parser.add_argument("url", help="URL to request")
    args = parser.parse_args(argv)

    # Variables already set in the environment take precedence over the file.
    load_dotenv(find_dotenv(usecwd=True))

    try:
        if args.command == "get":
            print(json.dumps(get(args.url), indent=2))
        else:
            pay(args.url)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        print(f"error: request to {args.url} failed: {exc}", file=sys.stderr)
        return 1
    except PaymentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
