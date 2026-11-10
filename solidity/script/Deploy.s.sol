// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Script, console} from "forge-std/Script.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

import {GatedTransfer} from "../src/GatedTransfer.sol";
import {ISP1Verifier} from "../src/ISP1Verifier.sol";

/// @notice Deploys GatedTransfer to Base, behind an ERC1967 proxy.
/// @dev Reads OWNER, TREASURY, X402_CLIENT and PROGRAM_VKEY from the environment.
contract Deploy is Script {
    uint256 public constant BASE_CHAIN_ID = 8453;

    /// @notice Succinct's SP1VerifierGateway for Groth16 proofs on Base, which zkp-prove uses
    /// by default. From https://github.com/succinctlabs/sp1-contracts/tree/main/contracts/deployments
    address public constant BASE_SP1_VERIFIER_GATEWAY_GROTH16 = 0x397A5f7f3dBd538f23DE225B51f532c34448dA9B;

    /// @notice Circle's USDC on Base.
    address public constant BASE_USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;

    function run() external returns (GatedTransfer gated) {
        address owner = vm.envAddress("OWNER");
        address treasury = vm.envAddress("TREASURY");
        address x402Client = vm.envAddress("X402_CLIENT");
        bytes32 programVKey = vm.envBytes32("PROGRAM_VKEY");

        vm.startBroadcast();
        gated = deploy(owner, treasury, x402Client, programVKey);
        vm.stopBroadcast();

        console.log("GatedTransfer proxy:", address(gated));
        console.log("Implementation:", implementationOf(address(gated)));
    }

    function deploy(address owner, address treasury, address x402Client, bytes32 programVKey)
        public
        returns (GatedTransfer)
    {
        require(block.chainid == BASE_CHAIN_ID, "Deploy: not Base mainnet");
        require(BASE_SP1_VERIFIER_GATEWAY_GROTH16.code.length > 0, "Deploy: no verifier at the address");
        require(BASE_USDC.code.length > 0, "Deploy: no USDC at the address");

        GatedTransfer implementation =
            new GatedTransfer(ISP1Verifier(BASE_SP1_VERIFIER_GATEWAY_GROTH16), IERC20(BASE_USDC));
        ERC1967Proxy proxy = new ERC1967Proxy(
            address(implementation),
            abi.encodeCall(GatedTransfer.initialize, (owner, treasury, x402Client, programVKey))
        );
        return GatedTransfer(address(proxy));
    }

    function implementationOf(address proxy) public view returns (address) {
        // The ERC1967 implementation slot: bytes32(uint256(keccak256("eip1967.proxy.implementation")) - 1)
        bytes32 slot = 0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;
        return address(uint160(uint256(vm.load(proxy, slot))));
    }
}
