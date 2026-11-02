"""Platform-specific s2.cpp paths and accelerator arguments."""

import sys
from pathlib import Path


PROFILES = {
    "win32": {
        "platform": "windows",
        "executable": Path("s2-build/Release/s2.exe"),
        "accelerator": "cuda",
        "setup": "setup-s2.ps1",
    },
    "darwin": {
        "platform": "macos",
        "executable": Path("s2-build/s2"),
        "accelerator": "metal",
        "setup": "setup-s2.sh",
    },
}


def current(platform=None):
    """Return the supported runtime profile for this OS."""
    platform = platform or sys.platform
    if platform not in PROFILES:
        raise ValueError(f"Unsupported platform: {platform} (use Windows or macOS)")
    return PROFILES[platform]


def accelerator(settings, platform=None):
    """Resolve auto to the platform default and validate an explicit accelerator."""
    profile = current(platform)
    selected = settings.get("accelerator", "auto")
    if not isinstance(selected, str):
        raise ValueError("server.accelerator must be auto, cuda, metal, or cpu")
    selected = selected.lower()
    selected = profile["accelerator"] if selected == "auto" else selected
    if selected not in {"cuda", "metal", "cpu"}:
        raise ValueError("server.accelerator must be auto, cuda, metal, or cpu")
    return selected


def accelerator_args(settings, platform=None):
    """Build s2.cpp accelerator flags; auto selects CUDA on Windows and Metal on macOS."""
    selected = accelerator(settings, platform)
    layers = int(settings.get("gpu_layers", 36))
    device = str(int(settings.get("gpu_device", 0)))
    if selected == "cuda":
        return ["--cuda", device, "--gpu-layers", str(layers)]
    if selected == "metal":
        return ["--metal", "--gpu-layers", str(layers)]
    if selected == "cpu":
        return ["--gpu-layers", "0"]


def codec_arg(settings, platform=None):
    """Keep the codec on CPU for Metal, whose PAD operation is currently unsupported."""
    return ("--codec-follow-backend" if settings.get("codec_gpu", True)
            and accelerator(settings, platform) == "cuda" else "--codec-cpu")
