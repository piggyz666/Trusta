"""Where generated evidence files live: reader-facing docs stay in docs/, development records go to docs/archive/."""
from pathlib import Path

PUBLIC = {
    "agent-communication-demo.json", "agent-communication-trusted-registry.json",
    "phase4-security-benchmark.json", "phase5-security-benchmark.json", "scenario-audit.json",
}


def doc(root, name):
    """Path for a generated docs file under ``root`` (repo root, or a temporary copy of it)."""
    path = Path(root) / "docs" / (name if name in PUBLIC else Path("archive") / name)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
