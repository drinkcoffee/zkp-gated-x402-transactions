# Zero Knowledge Proof Gated x402 Transactions

This repo contains several projects that combine to deliver a system where x402 transactions only succeed if an AI agent presents a zero knowledge proof to the blockchain showing that the transaction is authorized.

## Architecture

![Architecture](docs/architecture.svg)

A user uses a command line application that is an AI agent, passing in a request (a text prompt). The AI agent (LangChain / python code in the [agent]{./agent} directory) calls to an LLM and then to a tool. The tool is implemented using an x402 server (Vercel application using python / FastAPI, in the [x402server](x402server) directory). The x402 server responds with payment information for the tool (which is acting as an x402 client). The tool creates a zkp using some rust code [zkp]{zkp} and the Succinct prover cloud. The zkp is presented in a transaction to transfer some USD from the agent's primary account to its payment account. The tool then submits the x402 transaction to the server, that then forwards it to the facilitator, that executes the payment. The tool then returns the information to the agent that is then able to utilise the information.
