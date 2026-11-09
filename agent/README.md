# x402 agent

Command line x402 client. It can report the x402 payment information a URL returns, and pay for the resource in USDC on Base.

## Usage

```sh
uv run x402-agent get <url>
```

The result is printed to stdout as JSON:

```json
{
  "http": {
    "url": "https://example.com/paid",
    "status": 402,
    "reason": "Payment Required",
    "http_version": "HTTP/1.1",
    "headers": {},
    "body": "{}"
  },
  "x402": {
    "version": 2,
    "source": "header:PAYMENT-REQUIRED",
    "payment_required": {
      "x402Version": 2,
      "resource": {"url": "https://example.com/paid"},
      "accepts": [
        {
          "scheme": "exact",
          "network": "eip155:84532",
          "amount": "10000",
          "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
          "payTo": "0x209693Bc6afc0C5328bA36FaF03C514EF312287C",
          "maxTimeoutSeconds": 60,
          "extra": {"name": "USDC", "version": "2"}
        }
      ]
    },
    "settlement": null,
    "errors": []
  }
}
```

- `http.body` is the response text. A body that is not valid UTF-8 is returned as `http.body_base64` instead.
- `x402.payment_required` and `x402.settlement` are the objects the server sent, unmodified. Amounts are strings in the token's atomic units.
- `x402.errors` lists x402 data that could not be decoded, and a 402 response with no x402 data.
- `x402` is `null` for a non-402 response with no x402 headers.

| Protocol | Payment requirements | Settlement result |
| --- | --- | --- |
| v2 | `PAYMENT-REQUIRED` header (base64 JSON) | `PAYMENT-RESPONSE` header (base64 JSON) |
| v1 | Response body (JSON) | `X-PAYMENT-RESPONSE` header (base64 JSON) |

If a response has both a valid `PAYMENT-REQUIRED` header and a valid body, the header is used.

The exit code is 0 whenever the server responds, including with a 402 or a 5xx. It is 1 if no response was received, and 2 for invalid arguments.

## Paying for a resource

**Approving a payment moves real USDC on Base mainnet.**

Copy `.env.example` to `.env`, set `X402_ACCOUNT_ADDRESS` and `X402_PRIVATE_KEY`, then run:

```sh
uv run x402-agent pay G <url>
uv run x402-agent pay P <url> '{"city": "Melbourne"}'
```

The first argument is the request type, in either case: `G` for GET or `P` for POST. With `P`, you can add JSON encoded parameters, which are sent unchanged as the request content with `Content-Type: application/json`. Without them, the POST has no content. Parameters that are not valid JSON, or that are given with `G`, are rejected with exit code 2 before any request is made.

```text
Service: Weather API
URL: https://example.com/paid
Description: Current weather
Payment: $0.01
Pay to: 0x209693Bc6afc0C5328bA36FaF03C514EF312287C

Your balance (0xYourAddress) is: USDC$ 12.34

Do you approve this transaction? 
```

Press `Y` or `y` to approve, or any other key to cancel. There's no need to press Enter.

Approving signs an EIP-3009 USDC transfer for exactly the amount and recipient in the payment option, and resends the original request with the signed payment in a `PAYMENT-SIGNATURE` header. The server submits the transfer and pays the gas, so the account needs no ETH. The command then prints the transaction, the HTTP status and the response content.

A failed payment is never retried automatically. If the command reports that a payment may still settle, check the account on chain before trying again.

A signed payment is never sent to a redirect target, and stays valid for at most an hour. It is not encrypted by the client, so only pay over `https://` URLs: anyone who intercepts it on a plain `http://` connection can submit it.

The command exits with code 1 and prints an error if:

- the response is not a 402, or has no valid `PAYMENT-REQUIRED` header
- there is no payment option for Base (`eip155:8453`)
- no Base option accepts USDC (`0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`)
- the balance cannot be read

It also exits with code 1 if the Base USDC options don't use the `exact` scheme with EIP-3009 transfers, if the balance is too low, or if the payment fails.

If several options match, the first is used. Amounts are shown to two decimal places: the payment rounds up and the balance rounds down.

## Configuration

Settings are read from a `.env` file in the current directory or the nearest parent directory that has one. `.env` is ignored by git. A variable already set in the environment takes precedence over the file.

| Variable | Purpose | Default |
| --- | --- | --- |
| `X402_ACCOUNT_ADDRESS` | Account that pays | None (required for `pay`) |
| `X402_PRIVATE_KEY` | Private key for that account | None (required to approve a payment) |
| `BASE_RPC_URL` | Base JSON-RPC endpoint | `https://mainnet.base.org` |

## Tests

```sh
uv run pytest
```
