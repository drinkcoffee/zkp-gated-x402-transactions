// SPDX-License-Identifier: MIT
pragma solidity ^0.8.36;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable2StepUpgradeable} from "@openzeppelin/contracts-upgradeable/access/Ownable2StepUpgradeable.sol";
import {Initializable} from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import {UUPSUpgradeable} from "@openzeppelin/contracts-upgradeable/proxy/utils/UUPSUpgradeable.sol";

import {ISP1Verifier} from "./ISP1Verifier.sol";

/// @title GatedTransfer
/// @notice Transfers USDC from the treasury to the x402 client, but only with a zero
/// knowledge proof from the circuit in ../zkp. The proof's only public value is the
/// payment amount, so the circuit has checked that the amount is at most $0.01.
/// @dev Deployed behind an ERC1967 proxy and upgraded with UUPS. The verifier and USDC
/// addresses are immutables of the implementation, so changing them needs an upgrade.
/// The treasury must have approved this contract (the proxy) to spend its USDC.
/// @dev keccak256(abi.encode(uint256(keccak256("zkpgated.storage.GatedTransfer")) - 1)) & ~bytes32(uint256(0xff))
contract GatedTransfer is Initializable, Ownable2StepUpgradeable, UUPSUpgradeable 
    layout at 0xdac00e02a38ebe4a8bb3c5296318f686d9c063e88cadded312c41482f1364400
{
    using SafeERC20 for IERC20;

    /// @notice Succinct's SP1 verifier, usually an SP1VerifierGateway.
    /// @custom:oz-upgrades-unsafe-allow state-variable-immutable
    ISP1Verifier public immutable VERIFIER;

    /// @notice The USDC token contract.
    /// @custom:oz-upgrades-unsafe-allow state-variable-immutable
    IERC20 public immutable USDC;

    event TreasurySet(address indexed previousTreasury, address indexed newTreasury);
    event X402ClientSet(address indexed previousX402Client, address indexed newX402Client);
    event ProgramVKeySet(bytes32 previousProgramVKey, bytes32 newProgramVKey);
    event GatedTransferExecuted(
        address indexed treasury, address indexed x402Client, uint256 paymentAmount, bytes32 indexed proofHash
    );

    error ZeroAddress();
    error ZeroProgramVKey();
    error NotX402Client(address caller);
    error ProofAlreadyUsed(bytes32 proofHash);

    // Address holding value that can be distributed to x402Client.
    address public treasury;

    // Address of operational x402Client.
    address public x402Client;

    // Program key corresponding to the zero knowledge circuit.
    bytes32 public programVKey;

    // Proofs that has already been presented.
    mapping(bytes32 proofHash => bool) private usedProofs;


    /// @param verifier Succinct's SP1 verifier, usually an SP1VerifierGateway.
    /// @param usdc The USDC token contract.
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor(ISP1Verifier verifier, IERC20 usdc) {
        require((address(verifier) != address(0) && address(usdc) != address(0)), ZeroAddress());
        VERIFIER = verifier;
        USDC = usdc;
        _disableInitializers();
    }

    /// @notice Sets up the proxy. Called once, when the proxy is deployed.
    /// @param initialOwner The owner, who can set the addresses and key, and upgrade.
    /// @param initialTreasury The account USDC is transferred from.
    /// @param initialX402Client The account USDC is transferred to, and the only caller of gatedTransfer.
    /// @param initialProgramVKey The verification key of the circuit.
    function initialize(
        address initialOwner,
        address initialTreasury,
        address initialX402Client,
        bytes32 initialProgramVKey
    ) external initializer {
        __Ownable_init(initialOwner);
        _setTreasury(initialTreasury);
        _setX402Client(initialX402Client);
        _setProgramVKey(initialProgramVKey);
    }

    /// @notice Verifies a proof that paymentAmount is allowed, then transfers paymentAmount
    /// USDC from the treasury to the x402 client.
    /// @dev Each proof can be used once. That is weak replay protection, because Groth16
    /// proofs can be altered into different bytes that still verify, and anyone can make a
    /// new valid proof. Restricting the caller to the x402 client is the main protection.
    /// @param paymentAmount The payment amount, in USDC's atomic units. It is the proof's public value.
    /// @param proofBytes The proof, as written by zkp-prove in `arguments.proofBytes`.
    function gatedTransfer(uint256 paymentAmount, bytes calldata proofBytes) external {
        require(msg.sender == x402Client, NotX402Client(msg.sender));

        bytes32 proofHash = keccak256(proofBytes);
        require(!usedProofs[proofHash], ProofAlreadyUsed(proofHash));
        usedProofs[proofHash] = true;

        // Reverts unless the proof is valid for this circuit and this payment amount.
        VERIFIER.verifyProof(programVKey, abi.encode(paymentAmount), proofBytes);

        USDC.safeTransferFrom(treasury, x402Client, paymentAmount);
        emit GatedTransferExecuted(treasury, x402Client, paymentAmount, proofHash);
    }

    /// @notice Sets the account USDC is transferred from.
    function setTreasury(address newTreasury) external onlyOwner {
        _setTreasury(newTreasury);
    }

    /// @notice Sets the account USDC is transferred to, and the only caller of gatedTransfer.
    function setX402Client(address newX402Client) external onlyOwner {
        _setX402Client(newX402Client);
    }

    /// @notice Sets the verification key of the circuit. Needed whenever the circuit changes.
    function setProgramVKey(bytes32 newProgramVKey) external onlyOwner {
        _setProgramVKey(newProgramVKey);
    }

    /// @notice Whether a proof has already been used for a transfer.
    function isProofUsed(bytes calldata proofBytes) external view returns (bool) {
        return usedProofs[keccak256(proofBytes)];
    }

    function _setTreasury(address newTreasury) private {
        require(newTreasury != address(0), ZeroAddress());
        emit TreasurySet(treasury, newTreasury);
        treasury = newTreasury;
    }

    function _setX402Client(address newX402Client) private {
        require(newX402Client != address(0), ZeroAddress());
        emit X402ClientSet(x402Client, newX402Client);
        x402Client = newX402Client;
    }

    function _setProgramVKey(bytes32 newProgramVKey) private {
        require(newProgramVKey != bytes32(0), ZeroProgramVKey());
        emit ProgramVKeySet(programVKey, newProgramVKey);
        programVKey = newProgramVKey;
    }

    function _authorizeUpgrade(address) internal override onlyOwner {}
}
