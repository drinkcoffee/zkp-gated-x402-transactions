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
/// knowledge proof from the circuit in ../zkp. The circuit has checked that the payment
/// amount is at most $0.01. The proof's public values are the chain ID, a nonce, the time
/// the proof was issued, and the payment amount. This contract only accepts a proof for
/// this chain, uses each nonce once, and rejects a proof older than proofValidity.
/// @dev Deployed behind an ERC1967 proxy and upgraded with UUPS. The verifier and USDC
/// addresses are immutables of the implementation, so changing them needs an upgrade.
/// The treasury must have approved this contract (the proxy) to spend its USDC.
/// @dev keccak256(abi.encode(uint256(keccak256("zkpgated.storage.GatedTransfer")) - 1)) & ~bytes32(uint256(0xff))
abstract contract GatedTransferLogicV1 is Initializable, Ownable2StepUpgradeable, UUPSUpgradeable 
{
    using SafeERC20 for IERC20;

    /// @notice How long a proof is valid for after it is issued, unless the owner changes it.
    uint256 public constant DEFAULT_PROOF_VALIDITY = 10 minutes;

    /// @notice Succinct's SP1 verifier, usually an SP1VerifierGateway.
    /// @custom:oz-upgrades-unsafe-allow state-variable-immutable
    ISP1Verifier public immutable VERIFIER;

    /// @notice The USDC token contract.
    /// @custom:oz-upgrades-unsafe-allow state-variable-immutable
    IERC20 public immutable USDC;

    event TreasurySet(address indexed previousTreasury, address indexed newTreasury);
    event X402ClientSet(address indexed previousX402Client, address indexed newX402Client);
    event ProgramVKeySet(bytes32 previousProgramVKey, bytes32 newProgramVKey);
    event ProofValiditySet(uint256 previousProofValidity, uint256 newProofValidity);
    event GatedTransferExecuted(
        address indexed treasury, address indexed x402Client, uint256 paymentAmount, uint256 indexed nonce
    );

    error ZeroAddress();
    error ZeroProgramVKey();
    error NotX402Client(address caller);
    error ZeroProofValidity();
    error NonceAlreadyUsed(uint256 nonce);
    error ProofIssuedInFuture(uint256 issuedAt, uint256 blockTimestamp);
    error ProofExpired(uint256 issuedAt, uint256 blockTimestamp);

    // Address holding value that can be distributed to x402Client.
    address public treasury;

    // Address of operational x402Client.
    address public x402Client;

    // Program key corresponding to the zero knowledge circuit.
    bytes32 public programVKey;

    // How long, in seconds, a proof is valid for after the time it was issued.
    uint256 public proofValidity;

    // Nonces of proofs that have already been used.
    mapping(uint256 nonce => bool) private usedNonces;


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
    /// @dev Proofs are valid for DEFAULT_PROOF_VALIDITY until the owner calls setProofValidity.
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
        _setProofValidity(DEFAULT_PROOF_VALIDITY);
    }

    /// @notice Verifies a proof that paymentAmount is allowed, then transfers paymentAmount
    /// USDC from the treasury to the x402 client.
    /// @dev The nonce is a public value, so each proof works once, however its bytes are
    /// altered. Anyone can still make a new valid proof with a new nonce, so restricting
    /// the caller to the x402 client is the main protection.
    /// @param nonce The proof's nonce. Each nonce can be used once.
    /// @param issuedAt The Unix time the proof was issued.
    /// @param paymentAmount The payment amount, in USDC's atomic units.
    /// @param proofBytes The proof, as written by zkp-prove in `gatedTransfer.arguments.proofBytes`.
    function gatedTransfer(uint256 nonce, uint256 issuedAt, uint256 paymentAmount, bytes calldata proofBytes)
        external
    {
        require(msg.sender == x402Client, NotX402Client(msg.sender));
        // Block timestamps are safe for a limit of minutes. On Base, each block is exactly two
        // seconds after the last, so the sequencer cannot move them.
        // forge-lint: disable-next-line(block-timestamp)
        require(issuedAt <= block.timestamp, ProofIssuedInFuture(issuedAt, block.timestamp));
        // Subtracting, rather than adding to issuedAt, so a large proofValidity cannot overflow.
        // forge-lint: disable-next-line(block-timestamp)
        require(block.timestamp - issuedAt <= proofValidity, ProofExpired(issuedAt, block.timestamp));
        require(!usedNonces[nonce], NonceAlreadyUsed(nonce));
        usedNonces[nonce] = true;

        // Reverts unless the proof is valid for this circuit and exactly these public values.
        // Using block.chainid means a proof for any other chain fails to verify.
        VERIFIER.verifyProof(programVKey, abi.encode(block.chainid, nonce, issuedAt, paymentAmount), proofBytes);

        // This "arbitrary from" transfer is OK because setting the treasury is onlyOwner.
        // forge-lint: disable-next-line(arbitrary-send-erc20)
        USDC.safeTransferFrom(treasury, x402Client, paymentAmount);
        emit GatedTransferExecuted(treasury, x402Client, paymentAmount, nonce);
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

    /// @notice Sets how long, in seconds, a proof is valid for after the time it was issued.
    function setProofValidity(uint256 newProofValidity) external onlyOwner {
        _setProofValidity(newProofValidity);
    }

    /// @notice Whether a nonce has already been used for a transfer.
    function isNonceUsed(uint256 nonce) external view returns (bool) {
        return usedNonces[nonce];
    }

    function _setTreasury(address newTreasury) private {
        require(newTreasury != address(0), ZeroAddress());
        // There is no external call. The lint mistakes the inline assembly that OpenZeppelin's
        // onlyOwner and initializer use to read their storage for one.
        // forge-lint: disable-next-line(reentrancy-events)
        emit TreasurySet(treasury, newTreasury);
        treasury = newTreasury;
    }

    function _setX402Client(address newX402Client) private {
        require(newX402Client != address(0), ZeroAddress());
        // There is no external call. The lint mistakes the inline assembly that OpenZeppelin's
        // onlyOwner and initializer use to read their storage for one.
        // forge-lint: disable-next-line(reentrancy-events)
        emit X402ClientSet(x402Client, newX402Client);
        x402Client = newX402Client;
    }

    function _setProgramVKey(bytes32 newProgramVKey) private {
        require(newProgramVKey != bytes32(0), ZeroProgramVKey());
        // There is no external call. The lint mistakes the inline assembly that OpenZeppelin's
        // onlyOwner and initializer use to read their storage for one.
        // forge-lint: disable-next-line(reentrancy-events)
        emit ProgramVKeySet(programVKey, newProgramVKey);
        programVKey = newProgramVKey;
    }

    function _setProofValidity(uint256 newProofValidity) private {
        require(newProofValidity != 0, ZeroProofValidity());
        // There is no external call. The lint mistakes the inline assembly that OpenZeppelin's
        // onlyOwner and initializer use to read their storage for one.
        // forge-lint: disable-next-line(reentrancy-events)
        emit ProofValiditySet(proofValidity, newProofValidity);
        proofValidity = newProofValidity;
    }

    function _authorizeUpgrade(address) internal override onlyOwner {}
}
