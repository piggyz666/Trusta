pragma circom 2.1.6;

// TRUSTA-ANCHOR-M1-STATISTICS-V1, based on ANCHOR V21 Appendix B.
// Private statistics come from a pinned external evaluator, NOT arbitrary LLM
// inference inside this circuit. This is independent of BANKING_ELIGIBILITY_V1.
include "node_modules/circomlib/circuits/bitify.circom";
include "node_modules/circomlib/circuits/comparators.circom";
include "node_modules/circomlib/circuits/sha256/sha256.circom";

template BoundDigest() {
    signal input bits[256];
    signal input limbs[4];
    component packed[4];
    for (var i = 0; i < 4; i++) {
        packed[i] = Bits2Num(64);
        for (var j = 0; j < 64; j++) {
            packed[i].in[j] <== bits[i*64+63-j];
            bits[i*64+j] * (bits[i*64+j]-1) === 0;
        }
        packed[i].out === limbs[i];
    }
}

template CapabilityStatistics() {
    // Context order: statement, model, benchmark, evaluator, policy, encoding.
    signal input context[6][4];
    signal input context_bits[6][256];
    signal input scoreCommitment[4];
    signal input evaluationTranscript[4];
    signal input threshold;
    signal input tp;
    signal input fp;
    signal input fn;
    signal input score;
    signal input prediction_bits[256];
    signal input blinding_bits[128];

    component bindings[6];
    for (var i = 0; i < 6; i++) {
        bindings[i] = BoundDigest();
        for (var j = 0; j < 256; j++) bindings[i].bits[j] <== context_bits[i][j];
        for (var k = 0; k < 4; k++) bindings[i].limbs[k] <== context[i][k];
    }
    component tpBits = Num2Bits(32); tpBits.in <== tp;
    component fpBits = Num2Bits(32); fpBits.in <== fp;
    component fnBits = Num2Bits(32); fnBits.in <== fn;
    component scoreBits = Num2Bits(32); scoreBits.in <== score;
    component thresholdBits = Num2Bits(32); thresholdBits.in <== threshold;
    component scoreBound = LessEqThan(32);
    scoreBound.in[0] <== score; scoreBound.in[1] <== 1000000;
    scoreBound.out === 1;
    component thresholdBound = LessEqThan(32);
    thresholdBound.in[0] <== threshold; thresholdBound.in[1] <== 1000000;
    thresholdBound.out === 1;
    component admitted = LessEqThan(32);
    admitted.in[0] <== threshold; admitted.in[1] <== score;
    admitted.out === 1;
    signal denominator; denominator <== 2*tp+fp+fn;
    component denominatorBits = Num2Bits(34); denominatorBits.in <== denominator;
    component positive = LessThan(34);
    positive.in[0] <== 0; positive.in[1] <== denominator;
    positive.out === 1;
    signal remainder; remainder <== 2000000*tp-score*denominator;
    component remainderBits = Num2Bits(34); remainderBits.in <== remainder;
    component remainderBound = LessThan(34);
    remainderBound.in[0] <== remainder; remainderBound.in[1] <== denominator;
    remainderBound.out === 1;

    // Score commitment exactly SHA256(uint32BE(score) || private 16-byte blind).
    component commitment = Sha256(160);
    for (var sb = 0; sb < 32; sb++) commitment.in[sb] <== scoreBits.out[31-sb];
    for (var bb = 0; bb < 128; bb++) {
        blinding_bits[bb]*(blinding_bits[bb]-1) === 0;
        commitment.in[32+bb] <== blinding_bits[bb];
    }
    component commitmentBinding = BoundDigest();
    for (var cb = 0; cb < 256; cb++) commitmentBinding.bits[cb] <== commitment.out[cb];
    for (var cl = 0; cl < 4; cl++) commitmentBinding.limbs[cl] <== scoreCommitment[cl];

    // Explicit protocol-domain preimage: ASCII TRUSTAM1STATv1, then 6 context
    // digests, prediction artifact digest, and uint32BE TP/FP/FN/score.
    var tag[14] = [84,82,85,83,84,65,77,49,83,84,65,84,118,49];
    component transcript = Sha256(112+1792+128);
    for (var tb = 0; tb < 14; tb++) {
        for (var bit = 0; bit < 8; bit++) transcript.in[tb*8+bit] <== (tag[tb] >> (7-bit)) & 1;
    }
    for (var ctx = 0; ctx < 6; ctx++) {
        for (var b = 0; b < 256; b++) transcript.in[112+ctx*256+b] <== context_bits[ctx][b];
    }
    for (var pb = 0; pb < 256; pb++) {
        prediction_bits[pb]*(prediction_bits[pb]-1) === 0;
        transcript.in[112+1536+pb] <== prediction_bits[pb];
    }
    for (var mb = 0; mb < 32; mb++) {
        transcript.in[112+1792+mb] <== tpBits.out[31-mb];
        transcript.in[112+1792+32+mb] <== fpBits.out[31-mb];
        transcript.in[112+1792+64+mb] <== fnBits.out[31-mb];
        transcript.in[112+1792+96+mb] <== scoreBits.out[31-mb];
    }
    component transcriptBinding = BoundDigest();
    for (var eb = 0; eb < 256; eb++) transcriptBinding.bits[eb] <== transcript.out[eb];
    for (var el = 0; el < 4; el++) transcriptBinding.limbs[el] <== evaluationTranscript[el];
}

component main {public [context, scoreCommitment, evaluationTranscript, threshold]} = CapabilityStatistics();
