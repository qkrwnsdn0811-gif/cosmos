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


def test_gpu_batches_use_one_fixed_length():
    from nexus.device import padding_for
    # AMD ROCm on Windows rebuilds GPU kernels for every new input shape — variable-length batches stalled training
    assert padding_for("cuda", 128) == {"padding": "max_length", "max_length": 128, "truncation": True}
    assert padding_for("cpu", 128) == {"padding": True, "max_length": 128, "truncation": True}
