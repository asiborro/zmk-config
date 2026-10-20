#!/usr/bin/env python3
"""Refresh build inputs in a dedicated cache without touching its west modules."""
from pathlib import Path
import shutil
import sys

repo = Path(__file__).resolve().parents[1]
target = Path(sys.argv[1]).resolve()
marker = target / ".zmk-config-build-workspace"
if target == repo or repo in target.parents or target in repo.parents:
    raise SystemExit("Choose a separate build workspace, outside the repository")
if target.exists() and any(target.iterdir()) and not marker.exists():
    raise SystemExit("Build workspace must be empty or created by this tool")
if marker.exists() and marker.read_text().strip() != str(repo):
    raise SystemExit("Build workspace belongs to a different repository")
target.mkdir(parents=True, exist_ok=True)
marker.write_text(str(repo) + "\n")
for name in ("config", "scripts", "patches"):
    destination = target / name
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(repo / name, destination, ignore=shutil.ignore_patterns("__pycache__"))
shutil.copy2(repo / "build.yaml", target)
