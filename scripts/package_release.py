#!/usr/bin/env python
"""Build a distributable zip of this repository - dot-files included.

The reason this exists: updating by extracting a GitHub *zipball* over an
existing checkout silently drops every dot-prefixed path, because both
`cp -r src/* dst/` and the Windows Explorer copy skip them. Four things live
there and none of them are runtime code, so nothing breaks loudly:

    .gitattributes          pins *.sh to LF (without it setup.sh dies on line 1)
    .gitignore              build hygiene
    .github/workflows/ci.yml  the only CI, i.e. the tool-count consistency tests
    .claude/skills/**       the maintainer / production-operator skill packs

This script walks the tree with os.walk (which sees dot-files), writes
``dist/<name>-<version>.zip`` and ``dist/<name>-<version>.zip.sha256``, and
refuses to finish if any of those paths is missing.

Usage:
    python scripts/package_release.py [--out DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Never ship these: VCS, virtualenvs, caches, build output, local logs.
EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "build", "dist", "node_modules", ".idea", ".vscode",
}
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".egg-info")
EXCLUDE_FILES = {"catia_mcp.log"}

# Must survive packaging. Missing any of these means the archive is broken in a
# way nobody notices until CI is gone or setup.sh is CRLF.
REQUIRED = (
    ".gitattributes",
    ".gitignore",
    ".github/workflows/ci.yml",
    ".claude/skills/catia-v5-mcp-maintainer/SKILL.md",
    ".claude/skills/catia-v5-production-operator/SKILL.md",
    "pyproject.toml",
    "setup.sh",
    "catia_mcp/__init__.py",
    "tests",
)


def version() -> str:
    import re

    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as handle:
        match = re.search(r'^version\s*=\s*"([^"]+)"', handle.read(), re.M)
    return match.group(1) if match else "0.0.0"


def collect_files(root: str = ROOT) -> list[str]:
    """Every file to ship, as paths relative to ``root`` (dot-files included)."""
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for name in sorted(filenames):
            if name in EXCLUDE_FILES or name.endswith(EXCLUDE_SUFFIXES):
                continue
            full = os.path.join(dirpath, name)
            files.append(os.path.relpath(full, root).replace(os.sep, "/"))
    return files


def missing_required(files: list[str]) -> list[str]:
    present = set(files)
    missing = []
    for required in REQUIRED:
        if any(f == required or f.startswith(required + "/") for f in present):
            continue
        missing.append(required)
    return missing


def build(out_dir: str | None = None) -> tuple[str, str]:
    files = collect_files()
    missing = missing_required(files)
    if missing:
        raise SystemExit(
            "refusing to package: required path(s) absent from the tree: "
            + ", ".join(missing)
        )
    name = "MCP_CATIA_MechDog-%s" % version()
    out_dir = out_dir or os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    archive = os.path.join(out_dir, name + ".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in files:
            zf.write(os.path.join(ROOT, rel), arcname="%s/%s" % (name, rel))
    digest = hashlib.sha256(open(archive, "rb").read()).hexdigest()
    with open(archive + ".sha256", "w", encoding="utf-8", newline="\n") as handle:
        handle.write("%s  %s\n" % (digest, os.path.basename(archive)))
    return archive, digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="output directory (default: ./dist)")
    args = parser.parse_args(argv)
    archive, digest = build(args.out)
    files = collect_files()
    print("packaged %d files -> %s" % (len(files), archive))
    print("sha256 %s" % digest)
    print("dot-paths included: %s" % ", ".join(
        sorted(f for f in files if any(part.startswith(".") for part in f.split("/"))
               and not f.startswith(".git/"))[:6]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
