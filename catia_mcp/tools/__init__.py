"""CATIA V5 MCP Tools - solid-modelling plus the two output modules.

Nothing is dropped from upstream any more: ``knowledge`` came back in 0.3.63 and
``drawing`` in 0.3.64, each after its tools were exercised on B30 rather than
trusted. Everything that builds, measures or documents a solid is kept.
"""

from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.sketcher import SketcherTools
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.diagnostics import DiagnosticsTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.assembly import AssemblyTools
from catia_mcp.tools.measurement import MeasurementTools
from catia_mcp.tools.export import ExportTools
from catia_mcp.tools.knowledge import KnowledgeTools
from catia_mcp.tools.drawing import DraftingTools
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
    "KnowledgeTools",
    "DraftingTools",
    "PlannerTools",
]
