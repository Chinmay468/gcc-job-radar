#!/usr/bin/env python3
"""
install_tectonic.py — Automated installer for Tectonic LaTeX engine.

Downloads the official standalone Tectonic binary into tools/bin/
if not already present on PATH or in tools/bin/.
"""

import io
import os
from pathlib import Path
import platform
import shutil
import sys
import urllib.request
import zipfile
import tarfile

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = PROJECT_ROOT / "tools" / "bin"
BIN_DIR.mkdir(parents=True, exist_ok=True)

VERSION = "0.15.0"

URLS = {
    ("Windows", "AMD64"): f"https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40{VERSION}/tectonic-{VERSION}-x86_64-pc-windows-msvc.zip",
    ("Windows", "x86_64"): f"https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40{VERSION}/tectonic-{VERSION}-x86_64-pc-windows-msvc.zip",
    ("Linux", "x86_64"): f"https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40{VERSION}/tectonic-{VERSION}-x86_64-unknown-linux-gnu.tar.gz",
    ("Darwin", "x86_64"): f"https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40{VERSION}/tectonic-{VERSION}-x86_64-apple-darwin.tar.gz",
    ("Darwin", "arm64"): f"https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40{VERSION}/tectonic-{VERSION}-aarch64-apple-darwin.tar.gz",
}


def ensure_tectonic() -> str:
    """Ensure tectonic executable exists and return its path."""
    exe_name = "tectonic.exe" if platform.system() == "Windows" else "tectonic"
    target_path = BIN_DIR / exe_name

    if target_path.exists():
        return str(target_path)

    sys_which = shutil.which("tectonic")
    if sys_which:
        return sys_which

    system = platform.system()
    machine = platform.machine()
    key = (system, machine)
    url = URLS.get(key)

    if not url:
        raise RuntimeError(f"No pre-built Tectonic binary URL mapped for ({system}, {machine}).")

    print(f"[*] Downloading standalone Tectonic v{VERSION} from:\n    {url}")
    with urllib.request.urlopen(url) as resp:
        content = resp.read()

    print(f"[*] Extracting {exe_name} into {BIN_DIR}...")
    if url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for member in zf.namelist():
                if os.path.basename(member).lower() == exe_name.lower():
                    with zf.open(member) as src, open(target_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    break
    elif url.endswith(".tar.gz"):
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as tf:
            for member in tf.getmembers():
                if os.path.basename(member.name) == exe_name:
                    extracted = tf.extractfile(member)
                    if extracted:
                        with open(target_path, "wb") as dst:
                            shutil.copyfileobj(extracted, dst)
                    break

    if platform.system() != "Windows" and target_path.exists():
        target_path.chmod(0o755)

    print(f"[✔] Tectonic installed successfully at: {target_path}")
    return str(target_path)


if __name__ == "__main__":
    path = ensure_tectonic()
    print(f"Tectonic ready: {path}")
