// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC20Errors} from "@openzeppelin/contracts/interfaces/draft-IERC6093.sol";
import {OwnableUpgradeable} from "@openzeppelin/contracts-upgradeable/access/OwnableUpgradeable.sol";
import {Initializable} from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";

import {GatedTransfer} from "../src/GatedTransfer.sol";
import {ISP1Verifier} from "../src/ISP1Verifier.sol";

/// Accepts only the (programVKey, publicValues, proofBytes) combinations it was told about.
contract MockVerifier is ISP1Verifier {
    mapping(bytes32 => bool) public valid;

    error InvalidProof();

    function accept(bytes32 programVKey, bytes memory publicValues, bytes memory proofBytes) external {
        valid[keccak256(abi.encode(programVKey, publicValues, proofBytes))] = true;
    }

    function verifyProof(bytes32 programVKey, bytes calldata publicValues, bytes calldata proofBytes)
        external
        view
    {
        if (!valid[keccak256(abi.encode(programVKey, publicValues, proofBytes))]) revert InvalidProof();
    }
}

contract MockUSDC is ERC20 {
    constructor() ERC20("USD Coin", "USDC") {}

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

contract GatedTransferV2 is GatedTransfer {
    constructor(ISP1Verifier verifier, IERC20 usdc) GatedTransfer(verifier, usdc) {}

    function version() external pure returns (uint256) {
        return 2;
    }
}

contract GatedTransferTest is Test {
    bytes32 constant VKEY = 0x006e57c47adace60a9c1ed1caa782de1271ee08f45ee83abaf9f9d7bfe5962c9;
    bytes constant PROOF = hex"4388a21c0102030405";
    uint256 constant AMOUNT = 500;

    MockVerifier verifier;
    MockUSDC usdc;
    GatedTransfer gated;

    address owner = makeAddr("owner");
    address treasury = makeAddr("treasury");
    address client = makeAddr("x402Client");
    address stranger = makeAddr("stranger");

    function setUp() public {
        verifier = new MockVerifier();
        usdc = new MockUSDC();
        gated = deployProxy(owner, treasury, client, VKEY);

        usdc.mint(treasury, 1_000_000);
        vm.prank(treasury);
        usdc.approve(address(gated), type(uint256).max);

        verifier.accept(VKEY, abi.encode(AMOUNT), PROOF);
    }

    function deployProxy(address owner_, address treasury_, address client_, bytes32 vkey)
        internal
        returns (GatedTransfer)
    {
        GatedTransfer implementation = new GatedTransfer(verifier, usdc);
        bytes memory init = abi.encodeCall(GatedTransfer.initialize, (owner_, treasury_, client_, vkey));
        return GatedTransfer(address(new ERC1967Proxy(address(implementation), init)));
    }

    // Set up

    function test_initialize_sets_everything() public view {
        assertEq(gated.owner(), owner);
        assertEq(gated.treasury(), treasury);
        assertEq(gated.x402Client(), client);
        assertEq(gated.programVKey(), VKEY);
        assertEq(address(gated.VERIFIER()), address(verifier));
        assertEq(address(gated.USDC()), address(usdc));
    }

    function test_initialize_only_once() public {
        vm.expectRevert(Initializable.InvalidInitialization.selector);
        gated.initialize(stranger, stranger, stranger, VKEY);
    }

    function test_implementation_cannot_be_initialized() public {
        GatedTransfer implementation = new GatedTransfer(verifier, usdc);
        vm.expectRevert(Initializable.InvalidInitialization.selector);
        implementation.initialize(stranger, stranger, stranger, VKEY);
    }

    function test_constructor_rejects_zero_addresses() public {
        vm.expectRevert(GatedTransfer.ZeroAddress.selector);
        new GatedTransfer(ISP1Verifier(address(0)), usdc);
        vm.expectRevert(GatedTransfer.ZeroAddress.selector);
        new GatedTransfer(verifier, IERC20(address(0)));
    }

    function test_initialize_rejects_zero_values() public {
        GatedTransfer implementation = new GatedTransfer(verifier, usdc);
        bytes[4] memory inits = [
            abi.encodeCall(GatedTransfer.initialize, (address(0), treasury, client, VKEY)),
            abi.encodeCall(GatedTransfer.initialize, (owner, address(0), client, VKEY)),
            abi.encodeCall(GatedTransfer.initialize, (owner, treasury, address(0), VKEY)),
            abi.encodeCall(GatedTransfer.initialize, (owner, treasury, client, bytes32(0)))
        ];
        for (uint256 i = 0; i < inits.length; i++) {
            vm.expectRevert();
            new ERC1967Proxy(address(implementation), inits[i]);
        }
    }

    function test_storage_location_matches_erc7201() public pure {
        bytes32 expected =
            keccak256(abi.encode(uint256(keccak256("zkpgated.storage.GatedTransfer")) - 1)) & ~bytes32(uint256(0xff));
        assertEq(expected, 0xdac00e02a38ebe4a8bb3c5296318f686d9c063e88cadded312c41482f1364400);
    }

    // gatedTransfer

    function test_transfers_the_payment_amount_from_treasury_to_client() public {
        vm.expectEmit(address(gated));
        emit GatedTransfer.GatedTransferExecuted(treasury, client, AMOUNT, keccak256(PROOF));
        vm.prank(client);
        gated.gatedTransfer(AMOUNT, PROOF);

        assertEq(usdc.balanceOf(treasury), 1_000_000 - AMOUNT);
        assertEq(usdc.balanceOf(client), AMOUNT);
        assertTrue(gated.isProofUsed(PROOF));
    }

    function test_only_the_x402_client_can_call() public {
        vm.expectRevert(abi.encodeWithSelector(GatedTransfer.NotX402Client.selector, stranger));
        vm.prank(stranger);
        gated.gatedTransfer(AMOUNT, PROOF);

        vm.expectRevert(abi.encodeWithSelector(GatedTransfer.NotX402Client.selector, owner));
        vm.prank(owner);
        gated.gatedTransfer(AMOUNT, PROOF);
    }

    function test_rejects_an_invalid_proof() public {
        vm.expectRevert(MockVerifier.InvalidProof.selector);
        vm.prank(client);
        gated.gatedTransfer(AMOUNT, hex"4388a21cdead");
        assertEq(usdc.balanceOf(client), 0);
    }

    function test_rejects_a_proof_for_a_different_amount() public {
        vm.expectRevert(MockVerifier.InvalidProof.selector);
        vm.prank(client);
        gated.gatedTransfer(AMOUNT + 1, PROOF);
        assertEq(usdc.balanceOf(client), 0);
        assertFalse(gated.isProofUsed(PROOF));
    }

    function test_rejects_a_proof_for_a_different_program() public {
        vm.prank(owner);
        gated.setProgramVKey(bytes32(uint256(1)));
        vm.expectRevert(MockVerifier.InvalidProof.selector);
        vm.prank(client);
        gated.gatedTransfer(AMOUNT, PROOF);
    }

    function test_a_proof_can_be_used_once() public {
        vm.startPrank(client);
        gated.gatedTransfer(AMOUNT, PROOF);
        vm.expectRevert(abi.encodeWithSelector(GatedTransfer.ProofAlreadyUsed.selector, keccak256(PROOF)));
        gated.gatedTransfer(AMOUNT, PROOF);
        vm.stopPrank();
        assertEq(usdc.balanceOf(client), AMOUNT);
    }

    function test_fails_without_an_allowance() public {
        vm.prank(treasury);
        usdc.approve(address(gated), AMOUNT - 1);
        vm.expectRevert(
            abi.encodeWithSelector(
                IERC20Errors.ERC20InsufficientAllowance.selector, address(gated), AMOUNT - 1, AMOUNT
            )
        );
        vm.prank(client);
        gated.gatedTransfer(AMOUNT, PROOF);
        assertFalse(gated.isProofUsed(PROOF));
    }

    function test_uses_the_new_addresses_after_they_change() public {
        address newTreasury = makeAddr("newTreasury");
        address newClient = makeAddr("newClient");
        usdc.mint(newTreasury, AMOUNT);
        vm.prank(newTreasury);
        usdc.approve(address(gated), AMOUNT);

        vm.startPrank(owner);
        gated.setTreasury(newTreasury);
        gated.setX402Client(newClient);
        vm.stopPrank();

        vm.expectRevert(abi.encodeWithSelector(GatedTransfer.NotX402Client.selector, client));
        vm.prank(client);
        gated.gatedTransfer(AMOUNT, PROOF);

        vm.prank(newClient);
        gated.gatedTransfer(AMOUNT, PROOF);
        assertEq(usdc.balanceOf(newTreasury), 0);
        assertEq(usdc.balanceOf(newClient), AMOUNT);
        assertEq(usdc.balanceOf(treasury), 1_000_000);
    }

    // Owner functions

    function test_owner_sets_values_and_events_are_emitted() public {
        address newTreasury = makeAddr("newTreasury");
        address newClient = makeAddr("newClient");
        bytes32 newVKey = bytes32(uint256(2));

        vm.startPrank(owner);
        vm.expectEmit(address(gated));
        emit GatedTransfer.TreasurySet(treasury, newTreasury);
        gated.setTreasury(newTreasury);
        vm.expectEmit(address(gated));
        emit GatedTransfer.X402ClientSet(client, newClient);
        gated.setX402Client(newClient);
        vm.expectEmit(address(gated));
        emit GatedTransfer.ProgramVKeySet(VKEY, newVKey);
        gated.setProgramVKey(newVKey);
        vm.stopPrank();

        assertEq(gated.treasury(), newTreasury);
        assertEq(gated.x402Client(), newClient);
        assertEq(gated.programVKey(), newVKey);
    }

    function test_only_the_owner_sets_values() public {
        bytes memory notOwner = abi.encodeWithSelector(OwnableUpgradeable.OwnableUnauthorizedAccount.selector, client);
        vm.startPrank(client);
        vm.expectRevert(notOwner);
        gated.setTreasury(client);
        vm.expectRevert(notOwner);
        gated.setX402Client(client);
        vm.expectRevert(notOwner);
        gated.setProgramVKey(bytes32(uint256(2)));
        vm.stopPrank();
    }

    function test_setters_reject_zero_values() public {
        vm.startPrank(owner);
        vm.expectRevert(GatedTransfer.ZeroAddress.selector);
        gated.setTreasury(address(0));
        vm.expectRevert(GatedTransfer.ZeroAddress.selector);
        gated.setX402Client(address(0));
        vm.expectRevert(GatedTransfer.ZeroProgramVKey.selector);
        gated.setProgramVKey(bytes32(0));
        vm.stopPrank();
    }

    function test_ownership_transfer_takes_two_steps() public {
        vm.prank(owner);
        gated.transferOwnership(stranger);
        assertEq(gated.owner(), owner);
        vm.prank(stranger);
        gated.acceptOwnership();
        assertEq(gated.owner(), stranger);
    }

    // Upgrades

    function test_owner_can_upgrade_and_state_is_kept() public {
        vm.prank(client);
        gated.gatedTransfer(AMOUNT, PROOF);

        GatedTransferV2 v2 = new GatedTransferV2(verifier, usdc);
        vm.prank(owner);
        gated.upgradeToAndCall(address(v2), "");

        assertEq(GatedTransferV2(address(gated)).version(), 2);
        assertEq(gated.owner(), owner);
        assertEq(gated.treasury(), treasury);
        assertEq(gated.x402Client(), client);
        assertEq(gated.programVKey(), VKEY);
        assertTrue(gated.isProofUsed(PROOF));
    }

    function test_only_the_owner_can_upgrade() public {
        GatedTransferV2 v2 = new GatedTransferV2(verifier, usdc);
        vm.expectRevert(abi.encodeWithSelector(OwnableUpgradeable.OwnableUnauthorizedAccount.selector, stranger));
        vm.prank(stranger);
        gated.upgradeToAndCall(address(v2), "");
    }
}
