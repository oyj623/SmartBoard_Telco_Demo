#!/usr/bin/env python3
"""
Start Nusatel.

    python start.py

Seeds the database if it is missing, installs and builds the frontend, then
serves everything from one process on http://localhost:8000.

For frontend work you want hot reload instead:

    python -m uvicorn backend.main:app --reload --port 8000
    cd frontend && npm run dev            # serves 5173, proxies /api to 8000
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IS_WINDOWS = sys.platform == "win32"


def run(cmd, cwd=None, check=True):
    print(f"  $ {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    # npm and npx are .cmd shims on Windows and need a shell.
    use_shell = IS_WINDOWS if isinstance(cmd, list) else True
    result = subprocess.run(cmd, cwd=cwd or ROOT, shell=use_shell)
    if check and result.returncode != 0:
        print(f"ERROR: command failed with exit code {result.returncode}")
        sys.exit(1)
    return result


def build_frontend():
    print("\nBuilding the frontend…")
    frontend = ROOT / "frontend"
    if not (frontend / "node_modules").exists():
        print("  installing dependencies (this takes a minute the first time)")
        run(["npm", "install"], cwd=frontend)
    run(["npm", "run", "build"], cwd=frontend)
    print("  built OK")


def copy_dist():
    src = ROOT / "frontend" / "dist"
    dest = ROOT / "backend" / "static"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)


if __name__ == "__main__":
    print("Nusatel — startup")

    if not (ROOT / "data" / "my_states.geojson").exists():
        print("\nMap geometry missing — fetching it…")
        run([sys.executable, "data/build_geo.py"])

    if not (ROOT / "nusatel.db").exists():
        print("\nDatabase not found — seeding (about a minute, lands near 165 MB)…")
        run([sys.executable, "-m", "backend.seed.seed_all"])

    build_frontend()

    print("\nCopying build artifacts…")
    copy_dist()
    print("  done")

    print("\n" + "=" * 56)
    print("Nusatel")
    print("  Console   → http://localhost:8000")
    print("  Sign in   → hq / hq          (executive, all 16 states)")
    print("              north / north    (Perlis, Kedah, Penang, Perak)")
    print("              borneo / borneo  (Sabah, Sarawak, Labuan)")
    print("=" * 56 + "\n")

    run(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"],
        check=False,
    )
