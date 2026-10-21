from dataclasses import dataclass
import os
from pathlib import Path
import secrets


@dataclass
class Config:
    db_path: str = "data/trusta.sqlite3"
    high_risk_threshold: int = 500000
    action_ttl: int = 900
    authorization_ttl: int = 300
    contract_ttl: int = 60
    strong_auth_secret: str = ""
    api_token: str = ""
    signing_key: bytes = b""
    anomaly_large_amount: int = 100000
    anomaly_duplicate_seconds: int = 300
    llm_config_path: str = ""
    bank_config_path: str = ""
    # Python API compatibility profile; the desktop/server entry point selects V3 explicitly.
    communication_version: int = 2
    crypto_key_dir: str = ""
    blockchain_anchor_enabled: bool = False
    banking_version: int = 4  # Explicit historical Python profile; normal entry selects Phase 5.
    safety_profile: str = "legacy"  # Historical Python/test profile; local entry defaults to competition.
    daily_transfer_limit: int = 100000  # CNY minor units; other currencies require strong authorization.
    authentication_failure_limit: int = 5
    execution_failure_limit: int = 3
    security_operator_token: str = ""
    did_enabled: bool = False  # Additive did:key adapter; V3 URN envelope remains compatible.
    zkp_enabled: bool = False
    zkp_artifact_dir: str = ""
    zkp_manifest_sha256: str = ""  # Operator-pinned trust root, never supplied by a proof.
    finance_process_enabled: bool = False  # Explicit rollout, never silently fall back.
    agent_timeout_seconds: int = 20

    @classmethod
    def local(cls):
        root = Path(__file__).resolve().parents[1]
        env_file = root / ".env"
        if env_file.exists():
            from backend.security.local_files import protect_secret
            protect_secret(env_file)
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                    name, value = line.split("=", 1)
                    os.environ.setdefault(name.strip(), value.strip())
        data = root / "data"
        data.mkdir(exist_ok=True)
        key_file = data / "signing.key"
        if not key_file.exists():
            from backend.security.local_files import write_secret_atomic
            write_secret_atomic(key_file,secrets.token_bytes(32),create_only=True)
        from backend.security.local_files import protect_secret
        protect_secret(key_file)
        if len(key_file.read_bytes())!=32:
            from backend.domain import Rejected
            raise Rejected('INVALID_ROOT_SIGNING_KEY')
        return cls(db_path=os.environ.get("TRUSTA_DB", str(data / "trusta.sqlite3")),
                   high_risk_threshold=int(os.environ.get("TRUSTA_HIGH_RISK_CENTS", "500000")),
                   strong_auth_secret=os.environ.get("TRUSTA_STRONG_AUTH_SECRET", ""),
                   api_token=os.environ.get("TRUSTA_API_TOKEN", ""), signing_key=key_file.read_bytes(),
                   anomaly_large_amount=int(os.environ.get("TRUSTA_ANOMALY_LARGE_CENTS", "100000")),
                   anomaly_duplicate_seconds=int(os.environ.get("TRUSTA_ANOMALY_DUPLICATE_SECONDS", "300")),
                   llm_config_path=os.environ.get("TRUSTA_LLM_CONFIG", ""),
                   bank_config_path=os.environ.get("TRUSTA_BANK_CONFIG", ""),
                   communication_version=int(os.environ.get("TRUSTA_COMMUNICATION_VERSION", "3")),
                   crypto_key_dir=os.environ.get("TRUSTA_CRYPTO_KEY_DIR", ""),
                   blockchain_anchor_enabled=os.environ.get("TRUSTA_BLOCKCHAIN_ANCHOR_ENABLED", "false").lower() == "true",
                   banking_version=int(os.environ.get("TRUSTA_BANKING_VERSION", "5")),
                   safety_profile=os.environ.get("TRUSTA_SAFETY_PROFILE", "competition"),
                   daily_transfer_limit=int(os.environ.get("TRUSTA_DAILY_TRANSFER_CENTS", "100000")),
                   authentication_failure_limit=int(os.environ.get("TRUSTA_AUTH_FAILURE_LIMIT", "5")),
                   execution_failure_limit=int(os.environ.get("TRUSTA_EXECUTION_FAILURE_LIMIT", "3")),
                   security_operator_token=os.environ.get("TRUSTA_SECURITY_OPERATOR_TOKEN", ""),
                   did_enabled=os.environ.get('TRUSTA_DID_ENABLED','true').lower()=='true',
                   zkp_enabled=os.environ.get('TRUSTA_ZKP_ENABLED','false').lower()=='true',
                   zkp_artifact_dir=os.environ.get('TRUSTA_ZKP_ARTIFACT_DIR',''),
                   zkp_manifest_sha256=os.environ.get('TRUSTA_ZKP_MANIFEST_SHA256',''),
                   finance_process_enabled=os.environ.get('TRUSTA_FINANCE_PROCESS_ENABLED','false').lower()=='true',
                   agent_timeout_seconds=int(os.environ.get('TRUSTA_AGENT_TIMEOUT_SECONDS','20')))
