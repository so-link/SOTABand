"""Record actual model imports and dependency versions for reproducibility."""
import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path


def write_provenance(output):
    component = Path(__file__).resolve().parents[1]
    imports = {}
    for name, module in list(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if not filename or name.split(".")[0] not in {"scripts", "models", "layers", "utils"}:
            continue
        path = Path(filename).resolve()
        if not path.is_relative_to(component):
            raise RuntimeError(f"model implementation imported outside delivered component: {name}")
        imports[name] = {"path": path.relative_to(component).as_posix(),
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    result = {
        "entrypoint": str(Path(sys.argv[0]).resolve()),
        "python": sys.version, "executable": sys.executable,
        "component_root": str(component), "imports": imports,
        "packages": {name: importlib.metadata.version(name) for name in
                     ("torch", "numpy", "pandas", "scikit-learn", "scipy")},
        "model_source": json.loads((component / "vendor/Time-Series-Library/SOURCE.json").read_text(encoding="utf-8")),
    }
    Path(output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
