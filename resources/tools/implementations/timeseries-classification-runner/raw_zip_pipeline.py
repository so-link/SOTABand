"""Compatibility entry point; computation lives in the hypoxemia component."""
from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parents[3] / "workflows/hypoxemia/runtime/raw_zip_pipeline.py"), run_name="__main__")
