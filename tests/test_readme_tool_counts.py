# -*- coding: utf-8 -*-
"""The README's tool counts have to be the tool counts.

v0.3.2 shipped with three different numbers for the same thing: the header said
"87 tools", the group table summed to 86, and the server actually registered 94.
A user installing that release reported it - and it matters, because the whole
point of this project is "verification first", so a README that miscounts the
toolset is the one kind of wrong number a plan author will act on.

These tests read the README and compare it with the live tool definitions, so the
next tool that gets added fails the suite until the tables catch up.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

# README group label -> the Tools class that actually owns those tools.
MODULES: dict[str, tuple[str, str]] = {
    "Document": ("catia_mcp.tools.document", "DocumentTools"),
    "Sketcher": ("catia_mcp.tools.sketcher", "SketcherTools"),
    "Part Design": ("catia_mcp.tools.part_design", "PartDesignTools"),
    "Generative Shape Design": ("catia_mcp.tools.gsd", "GSDTools"),
    "Assembly": ("catia_mcp.tools.assembly", "AssemblyTools"),
    "Measurement": ("catia_mcp.tools.measurement", "MeasurementTools"),
    "Export": ("catia_mcp.tools.export", "ExportTools"),
    "Planner": ("catia_mcp.tools.planner", "PlannerTools"),
    "Diagnostics": ("catia_mcp.tools.diagnostics", "DiagnosticsTools"),
}

ROW = re.compile(r"^\|\s*([A-Za-z][A-Za-z ]*?)\s*\|\s*\*{0,2}(\d+)\*{0,2}\s*\|")


def readme_text() -> str:
    root = Path(__file__).resolve().parent.parent
    return (root / "README.md").read_text(encoding="utf-8")


def live_counts() -> dict[str, int]:
    out: dict[str, int] = {}
    for label, (module, cls) in MODULES.items():
        factory = getattr(importlib.import_module(module), cls)
        out[label] = len(factory(connection=None).get_tool_definitions())
    return out


def section(text: str, heading: str, stop: str = r"^#{2,4} ") -> str:
    """The body of a markdown section, up to the next heading.

    ``stop`` is the heading pattern that ends the section; the Added section
    contains a level-4 sub-heading, so it passes a level-2/3 pattern to keep it.
    """
    start = text.index(heading) + len(heading)
    rest = text[start:]
    match = re.search(stop, rest, re.M)
    return rest[: match.start()] if match else rest


def test_the_header_count_is_the_real_tool_count():
    live = live_counts()
    claimed = int(re.search(r"\*\*(\d+) tools\*\*", readme_text()).group(1))
    assert claimed == sum(live.values()), (
        f"README says {claimed} tools, the server registers {sum(live.values())}"
    )


def test_the_group_table_matches_module_by_module():
    table = section(readme_text(), "## Tool reference")
    documented = {}
    for line in table.splitlines():
        match = ROW.match(line)
        if match:
            documented[match.group(1).strip()] = int(match.group(2))

    live = live_counts()

    unknown = set(documented) - set(MODULES)
    assert not unknown, f"README lists groups with no module behind them: {unknown}"
    missing = set(MODULES) - set(documented)
    assert not missing, f"README is missing these groups: {missing}"

    wrong = {k: (documented[k], live[k]) for k in MODULES if documented[k] != live[k]}
    assert not wrong, "group counts (README, actual) disagree: " + repr(wrong)


def test_the_group_table_adds_up_to_the_header():
    """The version of this that shipped: header 87, table 86, actual 94."""
    table = section(readme_text(), "## Tool reference")
    documented = [int(m.group(2)) for line in table.splitlines()
                  if (m := ROW.match(line))]

    claimed = int(re.search(r"\*\*(\d+) tools\*\*", readme_text()).group(1))
    assert sum(documented) == claimed


def test_the_added_section_lists_as_many_tools_as_it_claims():
    """It said "Added (5 tools)" while the fork had added twelve."""
    body = section(readme_text(), "### Added", stop=r"^#{2,3} ")
    claimed = int(re.search(r"\((\d+) tools over upstream\)", body).group(1))
    listed = re.findall(r"^\| `(catia_[a-z0-9_]+)`", body, re.M)

    assert len(listed) == claimed, (
        f"heading claims {claimed} added tools, {len(listed)} are listed: {listed}"
    )
    assert len(set(listed)) == len(listed), "the same tool is listed twice"


def test_the_spec_subsection_heading_matches_its_rows():
    """It said "(8 tools)" while eleven rows were listed below it.

    The outer "### Added" heading is guarded above, but it stops at a level-3
    heading - which let the level-4 subsection drift unnoticed while 0.3.4 / 0.3.5
    / 0.3.8 added tools to it.
    """
    body = section(readme_text(), "#### Added while building parts from a spec")
    claimed = int(re.search(r"\((\d+) tools\)", body).group(1))
    listed = re.findall(r"^\| `(catia_[a-z0-9_]+)`", body, re.M)

    assert len(listed) == claimed, (
        f"subsection heading claims {claimed} tools, {len(listed)} are listed: {listed}"
    )


def test_the_readme_states_the_version_the_package_reports():
    """A visitor reads the intro line to know which release this page describes."""
    import catia_mcp

    head = readme_text().splitlines()[:5]
    stated = [m.group(1) for line in head if (m := re.match(r"\*\*(\d+\.\d+\.\d+)\*\*", line))]
    assert stated, "the intro no longer states a version under the title"
    assert stated[0] == catia_mcp.__version__, (
        f"README advertises {stated[0]}, the package reports {catia_mcp.__version__}"
    )


def test_the_server_reports_its_own_version_not_the_dependency_version():
    """serverInfo used to carry the mcp library's version (1.30.0) for a 0.3.2 server."""
    import catia_mcp
    from catia_mcp.server import CATIAMCPServer

    server = CATIAMCPServer().server
    assert server.version == catia_mcp.__version__


def test_setup_sh_installs_this_fork_not_upstream():
    """The script cloned daiemon12/catia-v5-mcp-server - the 98-tool upstream."""
    root = Path(__file__).resolve().parent.parent
    script = (root / "setup.sh").read_text(encoding="utf-8")

    clone_lines = [ln for ln in script.splitlines() if "git clone" in ln]
    assert clone_lines, "setup.sh no longer clones anything - did it change shape?"
    for line in clone_lines:
        assert "wudahua/MCP_CATIA_MechDog" in line, line
        assert "daiemon12/catia-v5-mcp-server" not in line, line

    assert "MCP_CATIA_MechDog" in script.split("INSTALL_DIR=")[1].splitlines()[0]
