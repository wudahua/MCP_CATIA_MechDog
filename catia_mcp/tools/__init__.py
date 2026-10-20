"""CATIA V5 MCP Tools - solid-modelling subset (lite fork).

Dropped relative to upstream: ``drawing`` (needs a Drafting licence, 2D output)
and ``knowledge`` (parameters / design tables). Everything that builds or
measures a solid is kept.
"""

from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.sketcher import SketcherTools
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.diagnostics import DiagnosticsTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.assembly import AssemblyTools
from catia_mcp.tools.measurement import MeasurementTools
from catia_mcp.tools.export import ExportTools
from catia_mcp.tools.planner import PlannerTools

__all__ = [
    "DocumentTools",
    "SketcherTools",
    "PartDesignTools",
    "GSDTools",
    "DiagnosticsTools",
    "AssemblyTools",
    "MeasurementTools",
    "ExportTools",
    "PlannerTools",
]
