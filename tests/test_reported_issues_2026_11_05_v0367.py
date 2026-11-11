"""The smoke script must scan what the router actually serves (0.3.67).

Reported 2026-11-05 (B32, v0.3.66):

    python tests/test_server.py   -> "Mismatch: 108 tools defined but 127 in
                                      router", exit 1
    pytest tests/test_server.py   -> 3 passed

Both numbers were real. ``_collect_tool_definitions()`` and ``test_imports()``
each held a hand-written list of nine modules, so ``knowledge.py`` (0.3.63, 6
tools) and ``drawing.py`` (0.3.64, 13 tools) were registered by the server and
validated by nothing. The equality assert lived only in ``main()``, i.e. the
strong check was the one CI never ran.

These tests pin the fix from the side that keeps it fixed: the scan must read
``server._tool_modules``, and it must still fail on a bad definition. A test
that only counts 127 would pass again the day someone pastes a new list in.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# tests/ has no __init__.py; pytest puts this directory on sys.path, and the
# explicit insert keeps the file runnable on its own too.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_server import (  # noqa: E402 - after the path line, deliberately
    _check_tools_match_router,
    _collect_tool_definitions,
)
# Imported under a non-test name: pytest collects anything called test_* in
# this module, and re-collecting test_server's own test would double every
# number this release is trying to make honest.
from test_server import _import_sweep  # noqa: E402


# ── stubs ────────────────────────────────────────────────────────────────────


def _tool(name, *, description="does something", schema=None):
    tool = {"name": name, "inputSchema": schema or {"type": "object"}}
    if description is not None:
        tool["description"] = description
    return tool


class _Module:
    def __init__(self, tools) -> None:
        self._tools = tools

    def get_tool_definitions(self):
        return self._tools


class _FakeServer:
    def __init__(self, modules, router=None) -> None:
        self._tool_modules = modules
        self._tool_router = (router if router is not None else
                             {tool["name"]: module
                              for module in modules
                              for tool in module.get_tool_definitions()})


# ── the scan follows the server, not a copy of its list ─────────────────────


def test_the_scan_reads_the_servers_own_module_list():
    server = _FakeServer([
        _Module([_tool("catia_alpha"), _tool("catia_beta")]),
        _Module([_tool("catia_gamma")]),
    ])
    assert _collect_tool_definitions(server) == 3, (
        "a hand-written module list would report the real server's 127 here; "
        "these three modules do not exist in the package at all")


def test_a_module_added_to_the_server_is_scanned_without_touching_the_test():
    one = _FakeServer([_Module([_tool("catia_alpha")])])
    two = _FakeServer([_Module([_tool("catia_alpha")]),
                       _Module([_tool("catia_late")])])
    assert _collect_tool_definitions(two) == _collect_tool_definitions(one) + 1


# ── and it still refuses a bad definition from any module ───────────────────


def test_a_missing_description_fails_the_scan():
    server = _FakeServer([_Module([_tool("catia_alpha", description=None)])])
    with pytest.raises(AssertionError) as caught:
        _collect_tool_definitions(server)
    assert "description" in str(caught.value)


def test_a_non_object_schema_fails_the_scan():
    server = _FakeServer([_Module([_tool("catia_alpha", schema={"type": "array"})])])
    with pytest.raises(AssertionError) as caught:
        _collect_tool_definitions(server)
    assert "type 'object'" in str(caught.value)


def test_a_duplicate_name_across_modules_fails_the_scan():
    server = _FakeServer([_Module([_tool("catia_same")]),
                          _Module([_tool("catia_same")])])
    with pytest.raises(AssertionError) as caught:
        _collect_tool_definitions(server)
    assert "Duplicate" in str(caught.value)


def test_a_name_without_the_prefix_fails_the_scan():
    server = _FakeServer([_Module([_tool("alpha")])])
    with pytest.raises(AssertionError) as caught:
        _collect_tool_definitions(server)
    assert "catia_" in str(caught.value)


def test_a_router_holding_an_extra_tool_fails_the_equality_check():
    modules = [_Module([_tool("catia_alpha")])]
    server = _FakeServer(modules,
                         router={"catia_alpha": modules[0], "catia_ghost": modules[0]})
    with pytest.raises(AssertionError) as caught:
        _check_tools_match_router(server)
    assert "catia_ghost" in str(caught.value)


def test_a_router_missing_a_defined_tool_fails_the_equality_check():
    modules = [_Module([_tool("catia_alpha"), _tool("catia_beta")])]
    server = _FakeServer(modules, router={"catia_alpha": modules[0]})
    with pytest.raises(AssertionError) as caught:
        _check_tools_match_router(server)
    assert "Mismatch" in str(caught.value) or "catia_beta" in str(caught.value)


# ── the import sweep covers the package, not nine names ─────────────────────


def test_imports_reach_every_module_file():
    import pkgutil

    import catia_mcp.tools as package

    files = [info.name for info in pkgutil.iter_modules(package.__path__)
             if not info.name.startswith("_")]
    assert _import_sweep() == len(files) >= 11
    for required in ("knowledge", "drawing"):
        assert required in files, (
            f"{required}.py is the module whose absence from the old list "
            "started this")


def test_the_real_server_still_matches_its_router():
    """The count matches, through the same function main() calls.

    130 since 0.3.79 added the three colour tools; the point of the assertion is
    that the number comes from ONE source, not that it is any particular value.
    """
    assert _check_tools_match_router() == 142


# ── the two hanging suggestions from the 0.3.60 report ─────────────────────


def test_the_two_edge_counts_point_at_each_other():
    """non_manifold_edges and excluded.ambiguous_edges are one rule, two names.

    Reported three releases running (0.3.60 → 0.3.66): a reader who sees two
    counts for ">2 triangles share this edge" has no way to tell they are the
    same rule with different scopes. The wording now says so in the tool
    description; this keeps it from being dropped as noise.
    """
    from catia_mcp.tools.measurement import MeasurementTools

    descriptions = {tool["name"]: tool["description"]
                    for tool in MeasurementTools(None).get_tool_definitions()}
    surface = descriptions["catia_check_surface"]
    assert "non_manifold_edges" in surface
    assert "excluded.ambiguous_edges" in surface
    assert "region" in surface, "the scope difference is the whole point"


def test_rotate_and_translate_record_what_creation_mode_measured():
    """The 0.3.60 suggestion asked whether copy-mode is explicit. It is.

    Measured on B30 2026-11-05: GetCreationMode reads 1 on a fresh rotate,
    SetCreationMode(0) reads back as 2, modes 1 and 2 read back as 1 - and no
    mode removes the source from the geometrical set. Nothing is set in code
    (the type library does not name the enum), but the finding is written where
    a caller reads it, so it is not re-investigated a fourth time.
    """
    from catia_mcp.tools.gsd import GSDTools

    descriptions = {tool["name"]: tool["description"]
                    for tool in GSDTools(None).get_tool_definitions()}
    for name in ("catia_gsd_rotate", "catia_gsd_translate"):
        text = descriptions[name]
        assert "CreationMode" in text, f"{name} should cite the measured property"
        assert "2026-11-05" in text, f"{name} should date the measurement"
