"""Which device the transformer models run on.

torch reports a usable GPU as "cuda" for both NVIDIA CUDA and AMD ROCm builds (this PC: RX 9070 XT with the AMD
ROCm 7.2.1 PyTorch wheels in nexus/.venv-rocm). The CPU venv simply reports no GPU. NEXUS_DEVICE forces a device.
"""
from __future__ import annotations

import importlib
import os


def pick_device(torch_module=None) -> str:
    override = os.environ.get("NEXUS_DEVICE")
    if override:
        return override
    torch = torch_module or importlib.import_module("torch")
    return "cuda" if torch.cuda.is_available() else "cpu"


def padding_for(device: str, max_length: int) -> dict:
    """Tokenizer padding arguments. On a GPU every batch gets the same length: AMD ROCm on Windows rebuilds kernels
    for each new input shape, and variable-length batches left the GPU idle while the CPU compiled (2026-10-18).
    On the CPU, padding to the longest sentence in the batch is cheaper."""
    if device == "cpu":
        return {"padding": True, "max_length": max_length, "truncation": True}
    return {"padding": "max_length", "max_length": max_length, "truncation": True}
