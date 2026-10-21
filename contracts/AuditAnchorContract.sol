// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// Audit commitments only; no banking data or execution authority.
contract AuditAnchorContract {
    address public immutable publisher;
    struct Commitment { bytes32 root; bytes32 identifier; uint64 sequence; uint32 version; }
    mapping(bytes32 => Commitment) private commitments;
    event Committed(bytes32 indexed checkpointHash, bytes32 root, bytes32 identifier, uint64 sequence, uint32 version);
    constructor() { publisher = msg.sender; }
    function commit(bytes32 checkpointHash, bytes32 root, bytes32 identifier, uint64 sequence, uint32 version) external {
        require(msg.sender == publisher, "publisher only");
        require(checkpointHash != bytes32(0) && root != bytes32(0) && version == 1, "invalid commitment");
        Commitment memory prior = commitments[checkpointHash];
        if (prior.root != bytes32(0)) {
            require(prior.root == root && prior.identifier == identifier && prior.sequence == sequence && prior.version == version, "duplicate conflict");
            return;
        }
        commitments[checkpointHash] = Commitment(root, identifier, sequence, version);
        emit Committed(checkpointHash, root, identifier, sequence, version);
    }
    function read(bytes32 checkpointHash) external view returns(bytes32 root, bytes32 identifier, uint64 sequence, uint32 version) {
        Commitment memory c = commitments[checkpointHash];
        return (c.root, c.identifier, c.sequence, c.version);
    }
    function verify(bytes32 checkpointHash, bytes32 root) external view returns(bool) {
        return root != bytes32(0) && commitments[checkpointHash].root == root;
    }
}
