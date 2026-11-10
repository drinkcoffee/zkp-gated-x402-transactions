// SPDX-License-Identifier: MIT
pragma solidity ^0.8.36;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

import {GatedTransferLogicV1} from "./GatedTransferLogicV1.sol";
import {ISP1Verifier} from "./ISP1Verifier.sol";

contract GatedTransfer is GatedTransferLogicV1 
    layout at 0xdac00e02a38ebe4a8bb3c5296318f686d9c063e88cadded312c41482f1364400
{
    constructor(ISP1Verifier verifier, IERC20 usdc) GatedTransferLogicV1(verifier, usdc) 
    {}
}
