"""Versioned extension boundaries. Phase3 compatibility keeps DisabledExtensions."""
from dataclasses import dataclass
import re
from typing import Protocol
from backend.domain import Rejected


class IdentityResolver(Protocol):
    def resolve(self, identifier: str) -> dict: ...


class SignatureVerifier(Protocol):
    def verify(self, envelope: dict, payload: object) -> None: ...


class AuditCommitter(Protocol):
    def commit(self, event_hashes: tuple[str, ...]) -> str: ...


@dataclass(frozen=True)
class AnchorCommitment:
    """An external anchor can receive only a digest, never an arbitrary payload or PII."""
    digest: str
    schema_version: int = 1

    def __post_init__(self):
        if not isinstance(self.digest, str) or not re.fullmatch(r"[0-9a-f]{64}", self.digest) or type(self.schema_version) is not int or self.schema_version != 1:
            raise Rejected("INVALID_ANCHOR_COMMITMENT")


class BlockchainAnchor(Protocol):
    def anchor(self, commitment: AnchorCommitment) -> str: ...


class ProofVerifier(Protocol):
    def verify(self, statement_hash: str, proof: bytes) -> bool: ...


class DisabledExtensions:
    NAMES = ("did", "ecdsa", "merkle_audit", "blockchain_anchor", "zkp")

    def status(self):
        return {name: {"enabled": False, "implemented": False} for name in self.NAMES}

    def require(self, name):
        raise Rejected("EXTENSION_DISABLED" if name in self.NAMES else "UNKNOWN_EXTENSION")


class Phase4Extensions:
    def __init__(self, service):
        self.service = service

    def status(self):
        return {"ecdsa": {"enabled": True, "implemented": True},
                "merkle_audit": {"enabled": True, "implemented": True},
                "local_anchor": {"enabled": not self.service.config.blockchain_anchor_enabled,
                                 "implemented": True, "is_blockchain": False},
                "blockchain_anchor": {"enabled": self.service.config.blockchain_anchor_enabled,
                                      "implemented": bool(self.service.anchors and self.service.anchors.adapter.name=='EXTERNAL_EVM_TESTNET'), "externally_verified": False},
                "did": {"enabled": self.service.config.did_enabled, "implemented": True, "method":"did:key", "financial_authority":False},
                "zkp": self.service.privacy.status() if self.service.privacy else
                       {"enabled":False,"implemented":True,"scheme":"Groth16","production_ready":False,"financial_authority":False}}

    def require(self, name):
        implementations = {"ecdsa": self.service.messages, "merkle_audit": self.service.checkpoints}
        if self.service.config.did_enabled:
            implementations['did']=self.service.did
        if self.service.privacy:
            implementations['zkp']=self.service.privacy
        if not self.service.config.blockchain_anchor_enabled:
            implementations["local_anchor"] = self.service.anchors
        elif self.service.anchors.adapter.name=='EXTERNAL_EVM_TESTNET':
            implementations['blockchain_anchor']=self.service.anchors
        if name in implementations:
            return implementations[name]
        raise Rejected("EXTENSION_DISABLED" if name in self.status() else "UNKNOWN_EXTENSION")
