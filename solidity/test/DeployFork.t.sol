// SPDX-License-Identifier: MIT
pragma solidity ^0.8.36;

import {Test} from "forge-std/Test.sol";
import {IERC20Metadata} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";

import {GatedTransfer} from "../src/GatedTransfer.sol";
import {Deploy} from "../script/Deploy.s.sol";

/// Runs the deployment script against a fork of Base. Skipped unless BASE_RPC_URL is set.
contract DeployForkTest is Test {
    bytes32 constant VKEY = 0x006e57c47adace60a9c1ed1caa782de1271ee08f45ee83abaf9f9d7bfe5962c9;

    Deploy script;
    GatedTransfer gated;
    address owner = makeAddr("owner");
    address treasury = makeAddr("treasury");
    address client = makeAddr("x402Client");

    function setUp() public {
        string memory rpcUrl = vm.envOr("BASE_RPC_URL", string(""));
        if (bytes(rpcUrl).length == 0) {
            vm.skip(true);
            return;
        }
        vm.createSelectFork(rpcUrl);
        script = new Deploy();
        gated = script.deploy(owner, treasury, client, VKEY);
    }

    function test_deploys_with_the_base_addresses() public view {
        assertEq(address(gated.VERIFIER()), script.BASE_SP1_VERIFIER_GATEWAY_GROTH16());
        assertEq(address(gated.USDC()), script.BASE_USDC());
        assertEq(IERC20Metadata(address(gated.USDC())).symbol(), "USDC");
        assertEq(IERC20Metadata(address(gated.USDC())).decimals(), 6);
        assertEq(gated.owner(), owner);
        assertEq(gated.treasury(), treasury);
        assertEq(gated.x402Client(), client);
        assertEq(gated.programVKey(), VKEY);
        assertEq(gated.proofValidity(), 10 minutes);
        assertEq(block.chainid, 8453);
        assertTrue(script.implementationOf(address(gated)) != address(0));
    }

    function test_the_real_verifier_rejects_a_fake_proof() public {
        // 0x4388a21c is the v6.1.0 Groth16 selector, so the gateway routes this to a real verifier.
        bytes memory fake = abi.encodePacked(bytes4(0x4388a21c), new bytes(256));
        vm.expectRevert();
        vm.prank(client);
        gated.gatedTransfer(1, block.timestamp, 500, fake);
    }
}
