"""Provision the public, pinned Chronos-2 checkpoint once, outside the order path."""
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from urllib.request import urlopen


MODEL_REVISION = "29ec3766d36d6f73f0696f85560a422f50e8498c"
CONFIG_SHA256 = "ef1143bfdc9c0376d9a056eefca46cb4b1ec3d0ffacd541ff56feb40fb708031"
WEIGHTS_SHA256 = "ddcda3c7508bf2528087723e98a20707cc04b7f370ae275a9fd88078ddba4f42"
_FILES = {"config.json": CONFIG_SHA256, "model.safetensors": WEIGHTS_SHA256}


def model_directory() -> Path:
    override = os.environ.get("BAKEPLAN_CHRONOS_MODEL_PATH")
    return Path(override) if override else Path.home() / ".cache" / "bakeplan" / MODEL_REVISION


def _verify(path: Path, expected: str) -> None:
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
            raise ValueError(f"Invalid Chronos-2 checkpoint file: {path.name}")


def verify_model(directory: Path) -> None:
    for name, expected in _FILES.items():
        _verify(directory / name, expected)


def provision(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, expected in _FILES.items():
        target = directory / name
        try:
            _verify(target, expected)
            continue
        except (FileNotFoundError, ValueError):
            pass
        url = f"https://huggingface.co/amazon/chronos-2/resolve/{MODEL_REVISION}/{name}"
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
            tmp = Path(stream.name)
        try:
            with urlopen(url, timeout=120) as src, tmp.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            _verify(tmp, expected)
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    provision(model_directory())
    print(f"Chronos-2 {MODEL_REVISION} ready at {model_directory()}")
