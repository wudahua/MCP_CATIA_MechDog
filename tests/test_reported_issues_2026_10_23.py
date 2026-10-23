"""Pins the fixes for the two 2026-10-23 field reports.

Report 1 (zipball): dot-paths vanish when an archive is copied over a checkout;
covered by tests/test_release_archive.py.

Report 2 (hole/loft/constraints/dirty state) - the parts that can be pinned
without a running CATIA live here; the COM behaviour itself is recorded in
CHANGELOG 0.3.4 with the measurements.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.measurement import MeasurementTools
from catia_mcp.tools.part_design import PartDesignTools


# ---- list_features: types come from the NLS prefix, not Shape.Type ---------

@pytest.mark.parametrize("name,kind", [
    ("凸台.1", "pad"),
    ("凹槽.3", "pocket"),
    ("孔.1", "hole"),
    ("旋转体.2", "shaft"),
    ("倒圆角.4", "fillet"),
    ("倒角.1", "chamfer"),
    ("拔模.1", "draft"),
    ("矩形阵列.1", "rect_pattern"),
    ("Pad.12", "pad"),
    ("Pocket.2", "pocket"),
    ("Hole.1", "hole"),
    ("EdgeFillet.3", "fillet"),
    ("SomeWeirdFeature.1", "unknown"),
])
def test_feature_kind(name: str, kind: str) -> None:
    assert PartDesignTools._feature_kind(name) == kind


def test_feature_kind_prefers_the_longer_prefix() -> None:
    # "倒圆角" must win over "倒角": a naive substring test would call a fillet
    # a chamfer.
    assert PartDesignTools._feature_kind("倒圆角.2") == "fillet"
    assert PartDesignTools._feature_kind("倒角.2") == "chamfer"


# ---- bounding box: unbounded wireframe points are dropped ------------------

def test_unbounded_vertices_are_filtered() -> None:
    points = [
        [0.0, 0.0, 0.0],
        [10.0, 5.0, 2.0],
        [1.0e7, 0.0, 0.0],      # a wireframe line's "infinite" end
        [-1.0e7, 0.0, 0.0],
        [float("nan"), 1.0, 1.0],
    ]
    kept, dropped = MeasurementTools._bounded(points)
    assert kept == [[0.0, 0.0, 0.0], [10.0, 5.0, 2.0]]
    assert dropped == 3


def test_a_normal_model_keeps_every_vertex() -> None:
    points = [[x, y, z] for x in (-20, 20) for y in (-10, 10) for z in (0, 10)]
    kept, dropped = MeasurementTools._bounded(points)
    assert (len(kept), dropped) == (8, 0)


# ---- save: a never-saved document is detected before Save() is attempted ---

class _Doc:
    def __init__(self, full_name: Any, raises: bool = False) -> None:
        self._full = full_name
        self._raises = raises
        self.Name = "Part1.CATPart"

    @property
    def FullName(self) -> Any:
        if self._raises:
            raise RuntimeError("no such property")
        return self._full


@pytest.mark.parametrize("full_name,has_path", [
    (r"C:/work/P05.CATPart", True),
    ("Part1.CATPart", False),
    ("", False),
])
def test_document_has_path(full_name: str, has_path: bool) -> None:
    assert DocumentTools._document_has_path(_Doc(full_name)) is has_path


def test_document_has_path_survives_a_missing_property() -> None:
    assert DocumentTools._document_has_path(_Doc(None, raises=True)) is False
