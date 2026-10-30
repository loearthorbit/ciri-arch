#!/usr/bin/env python3
"""Check authored scripts and pinned sources without executing builds or downloads."""

import ast
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent.parent
for path in sorted((root / "scripts").glob("*.sh")):
    subprocess.run(["bash", "-n", str(path)], check=True)
    print(f"Shell syntax: {path.name}")
for directory in ("scripts", "tests"):
    for path in sorted((root / directory).glob("*.py")):
        ast.parse(path.read_text(), filename=str(path))
        print(f"Python syntax: {directory}/{path.name}")
sources = json.loads((root / "sources.json").read_text())
for component, filename in (("cgpt", "build-cgpt-arm64.sh"), ("kernel", "build-ciri-kernel.sh")):
    revision = sources[component]["revision"]
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise SystemExit(f"Invalid pinned revision: {component}")
    if f"${{SOURCE_REVISION:-{revision}}}" not in (root / "scripts" / filename).read_text():
        raise SystemExit(f"Source pin differs between sources.json and {filename}")
print("Source pins match build scripts")
