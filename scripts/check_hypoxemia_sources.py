"""Verify that the delivered model implementation still matches its source manifest."""
import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    vendor = root / "resources/workflows/hypoxemia/runtime/vendor/Time-Series-Library"
    source = json.loads((vendor / "SOURCE.json").read_text(encoding="utf-8"))
    for entry in source["files"]:
        path = vendor / entry["path"]
        digest = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        if digest != entry["sha256"]:
            raise RuntimeError(f"Bundled implementation changed without provenance update: {entry['path']}")
    print(json.dumps({"version": source["delivery_version"], "commit": source["comparison_commit"],
                      "verified_files": len(source["files"])}, indent=2))


if __name__ == "__main__":
    main()
