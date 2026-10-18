from types import SimpleNamespace

from nexus.device import pick_device


def fake_torch(available: bool):
    return SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: available))


def test_uses_the_gpu_when_torch_sees_one(monkeypatch):
    monkeypatch.delenv("NEXUS_DEVICE", raising=False)
    assert pick_device(fake_torch(True)) == "cuda"          # NVIDIA CUDA and AMD ROCm both report as cuda
    assert pick_device(fake_torch(False)) == "cpu"


def test_environment_override(monkeypatch):
    monkeypatch.setenv("NEXUS_DEVICE", "cpu")
    assert pick_device(fake_torch(True)) == "cpu"
