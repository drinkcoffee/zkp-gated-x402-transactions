// SPDX-License-Identifier: MIT
pragma solidity ^0.8.36;

import {Test} from "forge-std/Test.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC20Errors} from "@openzeppelin/contracts/interfaces/draft-IERC6093.sol";
import {OwnableUpgradeable} from "@openzeppelin/contracts-upgradeable/access/OwnableUpgradeable.sol";
import {Initializable} from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";

import {GatedTransferLogicV1} from "../src/GatedTransferLogicV1.sol";
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

// Every version must use the same storage layout as GatedTransfer, or the upgrade loses its state.
contract GatedTransferV2 is GatedTransferLogicV1
    layout at 0xdac00e02a38ebe4a8bb3c5296318f686d9c063e88cadded312c41482f1364400
{
    constructor(ISP1Verifier verifier, IERC20 usdc) GatedTransferLogicV1(verifier, usdc) {}

    function version() external pure returns (uint256) {
        return 2;
    }
}

contract GatedTransferTest is Test {
    bytes32 constant VKEY = 0x006e57c47adace60a9c1ed1caa782de1271ee08f45ee83abaf9f9d7bfe5962c9;
    bytes constant PROOF = hex"4388a21c0102030405";
    uint256 constant AMOUNT = 500;
    uint256 constant BASE = 8453;
    uint256 constant NONCE = 2 ** 255 + 12345;
    uint256 constant ISSUED_AT = 1_800_000_000;

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

        vm.chainId(BASE);
        vm.warp(ISSUED_AT + 1 minutes);
        verifier.accept(VKEY, publicValues(BASE, NONCE, ISSUED_AT, AMOUNT), PROOF);
    }

    function deployProxy(address owner_, address treasury_, address client_, bytes32 vkey)
        internal
        returns (GatedTransfer)
    {
        GatedTransfer implementation = new GatedTransfer(verifier, usdc);
        bytes memory init = abi.encodeCall(GatedTransferLogicV1.initialize, (owner_, treasury_, client_, vkey));
        return GatedTransfer(address(new ERC1967Proxy(address(implementation), init)));
    }

    // Set up

    function test_initialize_sets_everything() public view {
        assertEq(gated.owner(), owner);
        assertEq(gated.treasury(), treasury);
        assertEq(gated.x402Client(), client);
        assertEq(gated.programVKey(), VKEY);
        assertEq(gated.proofValidity(), gated.DEFAULT_PROOF_VALIDITY());
        assertEq(gated.DEFAULT_PROOF_VALIDITY(), 600);
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
        vm.expectRevert(GatedTransferLogicV1.ZeroAddress.selector);
        new GatedTransfer(ISP1Verifier(address(0)), usdc);
        vm.expectRevert(GatedTransferLogicV1.ZeroAddress.selector);
        new GatedTransfer(verifier, IERC20(address(0)));
    }

    function test_initialize_rejects_zero_values() public {
        GatedTransfer implementation = new GatedTransfer(verifier, usdc);
        bytes[4] memory inits = [
            abi.encodeCall(GatedTransferLogicV1.initialize, (address(0), treasury, client, VKEY)),
            abi.encodeCall(GatedTransferLogicV1.initialize, (owner, address(0), client, VKEY)),
            abi.encodeCall(GatedTransferLogicV1.initialize, (owner, treasury, address(0), VKEY)),
            abi.encodeCall(GatedTransferLogicV1.initialize, (owner, treasury, client, bytes32(0)))
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

    function publicValues(uint256 chainId, uint256 nonce, uint256 issuedAt, uint256 amount)
        internal
        pure
        returns (bytes memory)
    {
        return abi.encode(chainId, nonce, issuedAt, amount);
    }

    /// Calls gatedTransfer as the x402 client with the default proof.
    function transfer() internal {
        vm.prank(client);
        gated.gatedTransfer(NONCE, ISSUED_AT, AMOUNT, PROOF);
    }

    function test_transfers_the_payment_amount_from_treasury_to_client() public {
        vm.expectEmit(address(gated));
        emit GatedTransferLogicV1.GatedTransferExecuted(treasury, client, AMOUNT, NONCE);
        transfer();

        assertEq(usdc.balanceOf(treasury), 1_000_000 - AMOUNT);
        assertEq(usdc.balanceOf(client), AMOUNT);
        assertTrue(gated.isNonceUsed(NONCE));
    }

    function test_only_the_x402_client_can_call() public {
        vm.expectRevert(abi.encodeWithSelector(GatedTransferLogicV1.NotX402Client.selector, stranger));
        vm.prank(stranger);
        gated.gatedTransfer(NONCE, ISSUED_AT, AMOUNT, PROOF);

        vm.expectRevert(abi.encodeWithSelector(GatedTransferLogicV1.NotX402Client.selector, owner));
        vm.prank(owner);
        gated.gatedTransfer(NONCE, ISSUED_AT, AMOUNT, PROOF);
    }

    function test_rejects_an_invalid_proof() public {
        vm.expectRevert(MockVerifier.InvalidProof.selector);
        vm.prank(client);
        gated.gatedTransfer(NONCE, ISSUED_AT, AMOUNT, hex"4388a21cdead");
        assertEq(usdc.balanceOf(client), 0);
        assertFalse(gated.isNonceUsed(NONCE));
    }

    function test_rejects_a_proof_for_a_different_amount() public {
        vm.expectRevert(MockVerifier.InvalidProof.selector);
        vm.prank(client);
        gated.gatedTransfer(NONCE, ISSUED_AT, AMOUNT + 1, PROOF);
        assertEq(usdc.balanceOf(client), 0);
    }

    function test_rejects_a_proof_for_a_different_nonce() public {
        vm.expectRevert(MockVerifier.InvalidProof.selector);
        vm.prank(client);
        gated.gatedTransfer(NONCE + 1, ISSUED_AT, AMOUNT, PROOF);
    }

    function test_rejects_a_proof_with_a_different_issue_time() public {
        vm.expectRevert(MockVerifier.InvalidProof.selector);
        vm.prank(client);
        gated.gatedTransfer(NONCE, ISSUED_AT - 1, AMOUNT, PROOF);
    }

    function test_rejects_a_proof_for_another_chain() public {
        // A valid proof for Ethereum mainnet is rejected on Base.
        bytes memory mainnetProof = hex"4388a21c01";
        verifier.accept(VKEY, publicValues(1, NONCE, ISSUED_AT, AMOUNT), mainnetProof);
        vm.expectRevert(MockVerifier.InvalidProof.selector);
        vm.prank(client);
        gated.gatedTransfer(NONCE, ISSUED_AT, AMOUNT, mainnetProof);

        // The Base proof is rejected on another chain.
        vm.chainId(1);
        vm.expectRevert(MockVerifier.InvalidProof.selector);
        transfer();
    }

    function test_rejects_a_proof_for_a_different_program() public {
        vm.prank(owner);
        gated.setProgramVKey(bytes32(uint256(1)));
        vm.expectRevert(MockVerifier.InvalidProof.selector);
        transfer();
    }

    function test_a_nonce_can_be_used_once() public {
        transfer();
        vm.expectRevert(abi.encodeWithSelector(GatedTransferLogicV1.NonceAlreadyUsed.selector, NONCE));
        transfer();

        // Altered proof bytes do not help, because the nonce is a public value.
        bytes memory altered = abi.encodePacked(PROOF, hex"00");
        verifier.accept(VKEY, publicValues(BASE, NONCE, ISSUED_AT, AMOUNT), altered);
        vm.expectRevert(abi.encodeWithSelector(GatedTransferLogicV1.NonceAlreadyUsed.selector, NONCE));
        vm.prank(client);
        gated.gatedTransfer(NONCE, ISSUED_AT, AMOUNT, altered);
        assertEq(usdc.balanceOf(client), AMOUNT);
    }

    function test_a_proof_is_valid_for_ten_minutes_by_default() public {
        assertEq(gated.proofValidity(), 10 minutes);
        vm.warp(ISSUED_AT + 10 minutes);
        transfer();
        assertEq(usdc.balanceOf(client), AMOUNT);
    }

    function test_rejects_an_expired_proof() public {
        vm.warp(ISSUED_AT + 10 minutes + 1);
        vm.expectRevert(
            abi.encodeWithSelector(GatedTransferLogicV1.ProofExpired.selector, ISSUED_AT, ISSUED_AT + 10 minutes + 1)
        );
        transfer();
        assertFalse(gated.isNonceUsed(NONCE));
    }

    function test_rejects_a_proof_issued_in_the_future() public {
        vm.warp(ISSUED_AT - 1);
        vm.expectRevert(
            abi.encodeWithSelector(GatedTransferLogicV1.ProofIssuedInFuture.selector, ISSUED_AT, ISSUED_AT - 1)
        );
        transfer();
    }

    function test_the_owner_can_change_the_time_limit() public {
        vm.prank(owner);
        gated.setProofValidity(1 hours);
        vm.warp(ISSUED_AT + 1 hours);
        transfer();

        bytes memory proof2 = hex"4388a21c02";
        verifier.accept(VKEY, publicValues(BASE, NONCE + 1, ISSUED_AT, AMOUNT), proof2);
        vm.prank(owner);
        gated.setProofValidity(30 seconds);
        vm.expectRevert(
            abi.encodeWithSelector(GatedTransferLogicV1.ProofExpired.selector, ISSUED_AT, ISSUED_AT + 1 hours)
        );
        vm.prank(client);
        gated.gatedTransfer(NONCE + 1, ISSUED_AT, AMOUNT, proof2);
    }

    function test_a_huge_time_limit_does_not_overflow() public {
        vm.prank(owner);
        gated.setProofValidity(type(uint256).max);
        vm.warp(type(uint256).max);
        transfer();
    }

    function testFuzz_only_proofs_within_the_time_limit_work(uint256 age) public {
        age = bound(age, 0, 365 days);
        vm.warp(ISSUED_AT + age);
        if (age > 10 minutes) {
            vm.expectRevert(
                abi.encodeWithSelector(GatedTransferLogicV1.ProofExpired.selector, ISSUED_AT, ISSUED_AT + age)
            );
        }
        transfer();
    }

    function test_fails_without_an_allowance() public {
        vm.prank(treasury);
        usdc.approve(address(gated), AMOUNT - 1);
        vm.expectRevert(
            abi.encodeWithSelector(
                IERC20Errors.ERC20InsufficientAllowance.selector, address(gated), AMOUNT - 1, AMOUNT
            )
        );
        transfer();
        assertFalse(gated.isNonceUsed(NONCE));
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

        vm.expectRevert(abi.encodeWithSelector(GatedTransferLogicV1.NotX402Client.selector, client));
        transfer();

        vm.prank(newClient);
        gated.gatedTransfer(NONCE, ISSUED_AT, AMOUNT, PROOF);
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
        emit GatedTransferLogicV1.TreasurySet(treasury, newTreasury);
        gated.setTreasury(newTreasury);
        vm.expectEmit(address(gated));
        emit GatedTransferLogicV1.X402ClientSet(client, newClient);
        gated.setX402Client(newClient);
        vm.expectEmit(address(gated));
        emit GatedTransferLogicV1.ProgramVKeySet(VKEY, newVKey);
        gated.setProgramVKey(newVKey);
        vm.expectEmit(address(gated));
        emit GatedTransferLogicV1.ProofValiditySet(10 minutes, 5 minutes);
        gated.setProofValidity(5 minutes);
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
        vm.expectRevert(notOwner);
        gated.setProofValidity(1 days);
        vm.stopPrank();
    }

    function test_setters_reject_zero_values() public {
        vm.startPrank(owner);
        vm.expectRevert(GatedTransferLogicV1.ZeroAddress.selector);
        gated.setTreasury(address(0));
        vm.expectRevert(GatedTransferLogicV1.ZeroAddress.selector);
        gated.setX402Client(address(0));
        vm.expectRevert(GatedTransferLogicV1.ZeroProgramVKey.selector);
        gated.setProgramVKey(bytes32(0));
        vm.expectRevert(GatedTransferLogicV1.ZeroProofValidity.selector);
        gated.setProofValidity(0);
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
        transfer();
        vm.prank(owner);
        gated.setProofValidity(1 hours);

        GatedTransferV2 v2 = new GatedTransferV2(verifier, usdc);
        vm.prank(owner);
        gated.upgradeToAndCall(address(v2), "");

        assertEq(GatedTransferV2(address(gated)).version(), 2);
        assertEq(gated.owner(), owner);
        assertEq(gated.treasury(), treasury);
        assertEq(gated.x402Client(), client);
        assertEq(gated.programVKey(), VKEY);
        assertEq(gated.proofValidity(), 1 hours);
        assertTrue(gated.isNonceUsed(NONCE));
    }

    function test_only_the_owner_can_upgrade() public {
        GatedTransferV2 v2 = new GatedTransferV2(verifier, usdc);
        vm.expectRevert(abi.encodeWithSelector(OwnableUpgradeable.OwnableUnauthorizedAccount.selector, stranger));
        vm.prank(stranger);
        gated.upgradeToAndCall(address(v2), "");
    }
}
