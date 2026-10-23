"""The release archive must carry the dot-paths a zipball copy silently drops.

Reported 2026-10-23: updating by extracting the GitHub zipball over an existing
checkout loses `.gitattributes`, `.gitignore`, `.github/workflows/ci.yml` and
`.claude/skills/**`, because `cp -r src/* dst/` (and Explorer) never copy
dot-prefixed entries. Nothing breaks at import time - the CI that guards the
tool-count documentation is simply gone - so the packaging script has to be
tested rather than trusted.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import zipfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location(
    "package_release", os.path.join(REPO, "scripts", "package_release.py"))
package_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package_release)


def test_collect_files_sees_dot_paths():
    files = package_release.collect_files()
    assert ".gitattributes" in files
    assert ".gitignore" in files
    assert ".github/workflows/ci.yml" in files
    assert any(f.startswith(".claude/skills/") for f in files)


def test_collect_files_skips_vcs_and_envs():
    files = package_release.collect_files()
    assert not [f for f in files if f.startswith(".git/")]
    assert not [f for f in files if f.startswith(".venv/")]
    assert not [f for f in files if "__pycache__" in f]


def test_required_paths_are_all_present():
    assert package_release.missing_required(package_release.collect_files()) == []


def test_missing_required_is_detected():
    assert package_release.missing_required(["pyproject.toml"]) != []


def test_build_writes_a_zip_that_contains_the_dot_paths(tmp_path):
    archive, digest = package_release.build(str(tmp_path))
    assert os.path.exists(archive)
    assert len(digest) == 64
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
    roots = "/".join(sorted({n.split("/")[0] for n in names}))
    assert roots.startswith("MCP_CATIA_MechDog-")
    inner = {n.split("/", 1)[1] for n in names}
    assert ".gitattributes" in inner
    assert ".github/workflows/ci.yml" in inner
    assert any(n.startswith("MCP_CATIA_MechDog-") and "/.claude/skills/" in n
               for n in names)
    manifest = open(archive + ".sha256", encoding="utf-8").read()
    assert digest in manifest


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
