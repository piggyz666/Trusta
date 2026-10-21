"""RFC6962-style binary tree over event hashes; this is not a CT log implementation."""
import hashlib
from backend.domain import Rejected
from backend.security.crypto import HEX


def leaf(event_hash):
    if not isinstance(event_hash, str) or not HEX.fullmatch(event_hash):
        raise Rejected("INVALID_MERKLE_LEAF")
    return hashlib.sha256(b"\x00" + bytes.fromhex(event_hash)).hexdigest()


def node(left, right):
    return hashlib.sha256(b"\x01" + bytes.fromhex(left) + bytes.fromhex(right)).hexdigest()


def split(count):
    return 1 << ((count - 1).bit_length() - 1)


def root(event_hashes):
    if not event_hashes:
        return hashlib.sha256(b"").hexdigest()
    if len(event_hashes) == 1:
        return leaf(event_hashes[0])
    k = split(len(event_hashes))
    return node(root(event_hashes[:k]), root(event_hashes[k:]))


def proof(event_hashes, index):
    if type(index) is not int or not 0 <= index < len(event_hashes):
        raise Rejected("INVALID_MERKLE_INDEX")
    if len(event_hashes) == 1:
        return []
    k = split(len(event_hashes))
    if index < k:
        return proof(event_hashes[:k], index) + [{"side": "RIGHT", "hash": root(event_hashes[k:])}]
    return proof(event_hashes[k:], index-k) + [{"side": "LEFT", "hash": root(event_hashes[:k])}]


def verify_inclusion_proof(event_hash, index, count, path, expected_root):
    """Exact path shape is derived from index and count, not trusted from the proof."""
    try:
        if (type(index) is not int or type(count) is not int or not 0 <= index < count <= 4096
                or not isinstance(path, list) or len(path) > 12
                or not isinstance(expected_root, str) or not HEX.fullmatch(expected_root)):
            return False
        def sides(i, n):
            if n == 1:
                return []
            k = split(n)
            return sides(i, k) + ["RIGHT"] if i < k else sides(i-k, n-k) + ["LEFT"]
        shape = sides(index, count)
        if len(path) != len(shape):
            return False
        result = leaf(event_hash)
        for item, side in zip(path, shape):
            if not isinstance(item, dict) or set(item) != {"side", "hash"} or item["side"] != side or not isinstance(item["hash"], str) or not HEX.fullmatch(item["hash"]):
                return False
            result = node(item["hash"], result) if side == "LEFT" else node(result, item["hash"])
        return result == expected_root
    except (Rejected, ValueError, TypeError, KeyError, RecursionError):
        return False
