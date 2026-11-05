"""Quick test to verify the MCP server loads correctly.

This test does NOT require CATIA V5 — it only checks that:
1. All modules import correctly
2. All tools are registered
3. Tool definitions are valid
"""

import sys
from pathlib import Path

# This file lives in tests/ so that `pytest tests` collects it - it used to sit
# at the repository root, which is why "pytest tests" reported 888 while
# "pytest tests test_server.py" reported 891, and a 2026-11-03 acceptance report
# read that 3-test gap as three tests having been dropped. One command, one
# number. The sys.path line keeps `python tests/test_server.py` working too.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _import_sweep():
    """Import every module under catia_mcp.tools; returns how many there were.

    0.3.67: the list is DISCOVERED, not written out. It used to name nine
    modules; knowledge.py (0.3.63) and drawing.py (0.3.64) were never added,
    so nineteen tools were registered by the server and checked by nothing
    here. A new file in that package is now imported and scanned the moment it
    exists.
    """
    import importlib
    import pkgutil

    import catia_mcp.tools as package

    names = sorted(info.name for info in pkgutil.iter_modules(package.__path__)
                   if not info.name.startswith("_"))
    for name in names:
        importlib.import_module(f"{package.__name__}.{name}")
    print(f"  All {len(names)} modules imported: {', '.join(names)}")
    return len(names)


def test_imports():
    """Pytest entry point; the work happens in _import_sweep."""
    print("Testing imports...")
    assert _import_sweep() >= 11


def _collect_tool_definitions(server=None):
    """Validate every tool the SERVER registers; returns the total count.

    0.3.67: the module list is the server's own ``_tool_modules`` - the same
    list ``_tool_router`` is built from. A copy kept here is exactly how this
    check came to validate 108 tools while the router served 127, and how
    ``python tests/test_server.py`` exited 1 while ``pytest tests`` stayed
    green: the equality assert lived only in main().
    """
    print("Testing tool definitions...")
    from catia_mcp.server import CATIAMCPServer

    server = server or CATIAMCPServer()

    total_tools = 0
    all_tool_names = set()

    for module in server._tool_modules:
        module_name = type(module).__name__.removesuffix("Tools")
        tools = module.get_tool_definitions()
        print(f"  {module_name}: {len(tools)} tools")

        for tool in tools:
            # Verify required fields
            assert "name" in tool, f"Tool missing 'name' in {module_name}"
            assert "description" in tool, f"Tool '{tool['name']}' missing 'description'"
            assert "inputSchema" in tool, f"Tool '{tool['name']}' missing 'inputSchema'"
            assert tool["inputSchema"]["type"] == "object", (
                f"Tool '{tool['name']}' inputSchema must be type 'object'"
            )

            # Check for duplicate names
            assert tool["name"] not in all_tool_names, (
                f"Duplicate tool name: {tool['name']}"
            )
            all_tool_names.add(tool["name"])

            # Check naming convention
            assert tool["name"].startswith("catia_"), (
                f"Tool '{tool['name']}' should start with 'catia_'"
            )

            total_tools += 1

    print(f"\n  Total: {total_tools} tools registered")
    print(f"  All tool names unique: yes")
    print(f"  All tools follow 'catia_*' naming: yes")
    return total_tools


def test_tool_definitions():
    """Pytest entry point; the work happens in _collect_tool_definitions."""
    assert _collect_tool_definitions() > 0


def _collect_server_creation(server=None):
    """Create the server without running it; returns the router size."""
    print("Testing server creation...")
    from catia_mcp.server import CATIAMCPServer

    server = server or CATIAMCPServer()
    tool_count = len(server._tool_router)
    print(f"  Server created with {tool_count} tools in router")
    return tool_count


def test_server_creation():
    """Pytest entry point; the work happens in _collect_server_creation."""
    assert _collect_server_creation() > 0


def _check_tools_match_router(server=None):
    """One server, both numbers, one assertion - shared by main() and pytest.

    The equality check used to exist only in ``main()``, which is why
    ``python tests/test_server.py`` exited 1 on 0.3.66 while
    ``python -m pytest tests -q`` reported 1100 passed: two commands, two
    answers, and the one with the strong assertion was not the one in CI.
    """
    from catia_mcp.server import CATIAMCPServer

    server = server or CATIAMCPServer()
    defined = _collect_tool_definitions(server)
    defined_names = {tool["name"] for module in server._tool_modules
                     for tool in module.get_tool_definitions()}
    assert defined_names == set(server._tool_router), (
        "the router holds tools no module defines, or the reverse: "
        f"{sorted(defined_names ^ set(server._tool_router))}"
    )
    routed = _collect_server_creation(server)
    assert defined == routed, (
        f"Mismatch: {defined} tools defined but {routed} in router"
    )
    return defined


def test_tool_definitions_match_the_router():
    """The equality check main() always did, now collected by pytest."""
    assert _check_tools_match_router() > 0


def main():
    print("=" * 60)
    print("CATIA V5 MCP Server — Verification Test")
    print("=" * 60)
    print()

    try:
        test_imports()
        print()

        total_tools = _check_tools_match_router()
        print()

        print("=" * 60)
        print(f"ALL TESTS PASSED — {total_tools} tools ready")
        print("=" * 60)
        return 0

    except Exception as e:
        print(f"\nTEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
