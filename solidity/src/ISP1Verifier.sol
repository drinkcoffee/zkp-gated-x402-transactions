// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title SP1 verifier interface
/// @notice The interface of Succinct's SP1 verifiers and SP1VerifierGateway, from
/// https://github.com/succinctlabs/sp1-contracts/blob/main/contracts/src/ISP1Verifier.sol
interface ISP1Verifier {
    /// @notice Verifies a proof with the given public values and program verification key.
    /// @dev Reverts if the proof is not valid. Returns nothing.
    /// @param programVKey The verification key of the SP1 program.
    /// @param publicValues The ABI encoded public values that the program committed.
    /// @param proofBytes The proof, starting with the four byte verifier selector.
    function verifyProof(bytes32 programVKey, bytes calldata publicValues, bytes calldata proofBytes)
        external
        view;
}
