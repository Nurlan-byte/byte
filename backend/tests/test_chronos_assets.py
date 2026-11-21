import hashlib
from io import BytesIO

import pytest

from backend import chronos_assets as assets


def test_verified_install_retries_without_exposing_partial_checkpoint(tmp_path, monkeypatch):
    payload = b"complete checkpoint"
    monkeypatch.setattr(assets, "_FILES", {"model.safetensors": hashlib.sha256(payload).hexdigest()})
    downloads = iter([b"incomplete", payload])
    monkeypatch.setattr(assets, "urlopen", lambda *args, **kwargs: BytesIO(next(downloads)))
    with pytest.raises(ValueError):
        assets.provision(tmp_path)
    assert not list(tmp_path.iterdir())
    assets.provision(tmp_path)
    assets.verify_model(tmp_path)
    assets.provision(tmp_path)  # No further download is available or needed.
    assert (tmp_path / "model.safetensors").read_bytes() == payload
