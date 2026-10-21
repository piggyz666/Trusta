pragma solidity 0.4.25;

// Separate test-consortium provenance contract. Never grants bank authority.
contract CollaborationAnchor {
    address public publisher;
    struct Round { bytes32 recordHash; bytes32 evidenceRoot; bytes32 snapshotHash;
                   bytes32 verdictHash; uint256 count; uint256 quorum; }
    mapping(bytes32 => Round) private rounds;
    event RoundCommitted(bytes32 indexed roundId, bytes32 recordHash, bytes32 evidenceRoot,
                         bytes32 snapshotHash, bytes32 verdictHash, uint256 count, uint256 quorum);
    constructor() public { publisher = msg.sender; }
    function commit(bytes32 id, bytes32 recordHash, bytes32 root, bytes32 snapshot,
                    bytes32 verdict, uint256 count, uint256 quorum) public {
        require(msg.sender == publisher, "publisher only");
        require(id != 0 && recordHash != 0 && snapshot != 0 && verdict != 0, "invalid commitment");
        require(quorum > 0 && quorum <= 6 && count <= 6, "invalid membership");
        Round storage prior = rounds[id];
        if (prior.recordHash != 0) {
            require(prior.recordHash == recordHash && prior.evidenceRoot == root &&
                    prior.snapshotHash == snapshot && prior.verdictHash == verdict &&
                    prior.count == count && prior.quorum == quorum, "conflicting round");
            return;
        }
        rounds[id] = Round(recordHash,root,snapshot,verdict,count,quorum);
        emit RoundCommitted(id,recordHash,root,snapshot,verdict,count,quorum);
    }
    function get(bytes32 id) public view returns(bytes32,bytes32,bytes32,bytes32,uint256,uint256) {
        Round storage r=rounds[id];
        return (r.recordHash,r.evidenceRoot,r.snapshotHash,r.verdictHash,r.count,r.quorum);
    }
}
