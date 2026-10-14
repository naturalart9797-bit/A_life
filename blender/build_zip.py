"""ドラッグ&ドロップでインストールできる拡張機能 zip を作る。

    python3 build_zip.py   ->  thorn_forest-<version>.zip
"""
import re
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
manifest = (HERE / "blender_manifest.toml").read_text(encoding="utf-8")
version = re.search(r'^version\s*=\s*"([^"]+)"', manifest, re.M).group(1)
out = HERE / f"thorn_forest-{version}.zip"

with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("blender_manifest.toml", manifest)
    zf.write(HERE / "thorn_forest.py", "__init__.py")
print(out)
