pragma circom 2.2.3;
include "node_modules/circomlib/circuits/poseidon.circom";
include "node_modules/circomlib/circuits/comparators.circom";
include "node_modules/circomlib/circuits/bitify.circom";

// Privacy statement only: a signed commitment's hidden balance and risk profile
// satisfy a public task's amount and required risk. This is never authorization.
template BankingEligibility() {
    signal input balance;
    signal input risk;
    signal input nonce;
    signal input amount;
    signal input requiredRisk;
    signal input context;
    signal output commitment;

    component balanceBits = Num2Bits(64); balanceBits.in <== balance;
    component amountBits = Num2Bits(64); amountBits.in <== amount;
    component riskBits = Num2Bits(3); riskBits.in <== risk;
    component requiredBits = Num2Bits(3); requiredBits.in <== requiredRisk;
    component nonceBits = Num2Bits(248); nonceBits.in <== nonce;
    component contextBits = Num2Bits(248); contextBits.in <== context;
    component sufficient = LessEqThan(64);
    sufficient.in[0] <== amount; sufficient.in[1] <== balance; sufficient.out === 1;
    component suitable = LessEqThan(3);
    suitable.in[0] <== requiredRisk; suitable.in[1] <== risk; suitable.out === 1;
    component riskMin = LessEqThan(3); riskMin.in[0] <== 1; riskMin.in[1] <== risk; riskMin.out === 1;
    component riskMax = LessEqThan(3); riskMax.in[0] <== risk; riskMax.in[1] <== 5; riskMax.out === 1;
    component reqMin = LessEqThan(3); reqMin.in[0] <== 1; reqMin.in[1] <== requiredRisk; reqMin.out === 1;
    component reqMax = LessEqThan(3); reqMax.in[0] <== requiredRisk; reqMax.in[1] <== 5; reqMax.out === 1;
    component positive = IsZero(); positive.in <== amount; positive.out === 0;
    component hash = Poseidon(4);
    hash.inputs[0] <== balance; hash.inputs[1] <== risk;
    hash.inputs[2] <== nonce; hash.inputs[3] <== context;
    commitment <== hash.out;
}
component main {public [amount, requiredRisk, context]} = BankingEligibility();
