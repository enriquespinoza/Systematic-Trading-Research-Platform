"""Usage: python -m integration_contracts --envelope ... --manifest ... --artifact-root ..."""
import argparse
import json
from pathlib import Path

from .validation import ContractError, verify_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify research-only cross-engine artifact evidence")
    parser.add_argument("--envelope", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--registry", type=Path, default=Path("config/engine_registry.json"))
    args = parser.parse_args()
    try:
        result = verify_bundle(args.envelope, args.manifest, args.artifact_root, args.registry)
    except (ContractError, OSError) as exc:
        parser.exit(2, f"CONTRACT_REJECTED: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
