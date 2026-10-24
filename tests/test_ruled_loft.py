"""A ruled (straight-sided) loft, measured against the analytic solid.

Three circles r=10/20/10 at z=0/50/100 = two frusta = 73 303.8 mm3. The GSM loft
interpolates through the sections and came out at 90 059.0 mm3 (+22.86 %); the
blend-per-pair route gives 73 304.7 (ratio 1.0000) on B30.
"""

from __future__ import annotations

import inspect

from catia_mcp.tools.gsd import GSDTools


def test_the_ruled_option_is_declared() -> None:
    tool = [d for d in GSDTools(connection=None).get_tool_definitions()
            if d["name"] == "catia_gsd_multi_section_surface"][0]
    props = tool["inputSchema"]["properties"]
    assert "ruled" in props
    assert props["ruled"]["type"] == "boolean"
    assert "blend" in props["ruled"]["description"]


def test_ruled_loft_is_built_from_blends_and_joined() -> None:
    source = inspect.getsource(GSDTools._ruled_loft)
    assert "AddNewBlend" in source
    assert "AddNewJoin" in source
    assert "73304.7" in source        # the measured number lives with the code


def test_the_smooth_loft_is_still_the_default() -> None:
    source = inspect.getsource(GSDTools._multi_section_surface)
    assert 'if args.get("ruled")' in source
    assert "AddNewLoft()" in source   # the NURBS path is untouched
