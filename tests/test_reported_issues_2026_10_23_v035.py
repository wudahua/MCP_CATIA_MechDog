"""Pins the fixes for the two v0.3.5 acceptance reports (2026-10-23).

The first report found that the volume gate added in 0.3.4 had made 0.3.3's
pocket auto-flip unreachable, and that an unmeasurable body was reported as
"volume verification unavailable on this installation" with no reason. The
second is about a launcher `.exe` locked by a running client during `pip
install`, which is a README/ordering fix and has no COM side.

Everything here runs against stubs: what CATIA does on B30/B32 is in CHANGELOG
0.3.6 with the measurements, but the decision logic is testable without it.
"""

from __future__ import annotations

from typing import Any

import pytest

from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.part_design import PartDesignTools


# ---------------------------------------------------------------- pocket stubs

class _Limit:
    LimitMode = 0


class _Feature:
    def __init__(self, name: str) -> None:
        self.Name = name
        self.DirectionOrientation = 0
        self.FirstLimit = _Limit()


class _Sketch:
    Name = "草图.1"


class _World:
    """A 8000 mm3 block that a pocket only cuts when it points into it.

    DirectionOrientation 1 is the one that reaches the material, which is what
    the field report measured on a top-face sketch: the default orientation
    comes out on the air side.
    """

    def __init__(self, cut_orientation: int | None = 1) -> None:
        self.cut_orientation = cut_orientation   # None = cuts nothing, ever
        self.tree: list[_Feature] = []


class _Measurable:
    def __init__(self, world: _World) -> None:
        self.world = world

    @property
    def Volume(self) -> float:
        cutting = any(f.DirectionOrientation == self.world.cut_orientation
                      for f in self.world.tree)
        if self.world.cut_orientation is None:
            cutting = False
        return (7748.7 if cutting else 8000.0) * 1e-9


class _Workbench:
    def __init__(self, world: _World) -> None:
        self.world = world

    def GetMeasurable(self, ref: Any) -> _Measurable:
        return _Measurable(self.world)


class _ShapeFactory:
    def __init__(self, world: _World) -> None:
        self.world = world

    def AddNewPocket(self, sketch: Any, depth: float) -> _Feature:
        feature = _Feature("凹槽.%d" % (len(self.world.tree) + 1))
        return feature


class _Part:
    def __init__(self, world: _World) -> None:
        self.world = world
        self.ShapeFactory = _ShapeFactory(world)
        self.InWorkObject: Any = None
        self.Parent = self

    def GetWorkbench(self, name: str) -> _Workbench:
        return _Workbench(self.world)

    def CreateReferenceFromObject(self, obj: Any) -> Any:
        return obj

    def Update(self) -> None:
        pass

    def UpdateObject(self, feature: Any) -> None:
        if feature not in self.world.tree:
            self.world.tree.append(feature)


class _Selection:
    def __init__(self, world: _World) -> None:
        self.world = world
        self.picked: list[Any] = []

    def Clear(self) -> None:
        self.picked = []

    def Add(self, obj: Any) -> None:
        self.picked.append(obj)

    def Delete(self) -> None:
        for obj in self.picked:
            if obj in self.world.tree:
                self.world.tree.remove(obj)


class _Conn:
    def __init__(self, world: _World) -> None:
        self.world = world
        self.part = _Part(world)
        self.body = _Body()
        self.last_sketch: Any = _Sketch()
        self.hso = _Selection(world)

    def ensure_connected(self) -> None:
        pass

    def get_active_part(self) -> Any:
        return self.part

    def get_active_part_body(self) -> Any:
        return self.body

    def refresh_display(self) -> None:
        pass


class _Body:
    Name = "PartBody"

    def __init__(self) -> None:
        self.Sketches = _Collection([_Sketch()])


def _tools(world: _World) -> tuple[PartDesignTools, _Conn]:
    conn = _Conn(world)
    return PartDesignTools(conn), conn  # type: ignore[arg-type]


# ------------------------------------------------- pocket: direction auto-flip

def test_pocket_flips_to_the_side_that_has_material() -> None:
    """The v0.3.3 behaviour 0.3.4 lost: no direction given, so flip and cut."""
    world = _World(cut_orientation=1)
    tools, _ = _tools(world)
    message = tools._pocket({"depth": 5})
    assert "auto-flipped" in message
    assert "251.3" in message          # 8000 - 7748.7
    assert len(world.tree) == 1         # the cut is kept


def test_pocket_rolls_back_when_both_directions_are_air() -> None:
    world = _World(cut_orientation=None)
    tools, _ = _tools(world)
    with pytest.raises(RuntimeError) as excinfo:
        tools._pocket({"depth": 5})
    assert "either direction" in str(excinfo.value)
    assert world.tree == []            # nothing left behind to poison updates


def test_explicit_direction_is_never_second_guessed() -> None:
    """'reverse' means reverse. Asking for the air side is an error, not a flip."""
    world = _World(cut_orientation=0)   # only orientation 0 cuts
    tools, _ = _tools(world)
    with pytest.raises(RuntimeError) as excinfo:
        tools._pocket({"depth": 5, "direction": "reverse"})   # -> orientation 1
    assert "the requested direction" in str(excinfo.value)
    assert world.tree == []


def test_pocket_says_why_the_volume_could_not_be_checked() -> None:
    """No volume is not the same as no cut: name the measurement that failed."""
    world = _World()
    tools, _ = _tools(world)
    tools._volume_probe = lambda part, body: (None, "the measurable's Volume failed: boom")  # type: ignore[method-assign]
    message = tools._pocket({"depth": 5})
    assert message.startswith("UNVERIFIED")
    assert "the measurable's Volume failed: boom" in message
    assert "this installation" not in message


# ------------------------------------------------------- volume probe: reasons

class _NoWorkbench:
    def GetWorkbench(self, name: str) -> Any:
        raise RuntimeError("no SPAWorkbench here")


class _BadMeasurable:
    @property
    def Volume(self) -> float:
        raise RuntimeError("E_FAIL")


def test_volume_probe_reports_the_workbench_failure() -> None:
    tools, _ = _tools(_World())
    part = _Part(_World())
    part.GetWorkbench = lambda name: (_ for _ in ()).throw(RuntimeError("E_FAIL"))  # type: ignore[assignment]
    volume, note = tools._volume_probe(part, _Body())
    assert volume is None
    assert "SPAWorkbench" in note and "E_FAIL" in note


def test_volume_probe_returns_the_number_when_it_works() -> None:
    tools, _ = _tools(_World())
    volume, note = tools._volume_probe(_Part(_World()), _Body())
    assert (volume, note) == (pytest.approx(8000.0), None)


# ----------------------------------------- activate_document: case-insensitive

class _DocStub:
    def __init__(self, name: str) -> None:
        self.Name = name
        self.activated = False

    def Activate(self) -> None:
        self.activated = True


class _Docs:
    def __init__(self, docs: list[_DocStub]) -> None:
        self.docs = docs
        self.Count = len(docs)

    def Item(self, index: int) -> _DocStub:
        return self.docs[index - 1]


class _DocConn:
    def __init__(self, docs: list[_DocStub]) -> None:
        self.documents = _Docs(docs)

    def ensure_connected(self) -> None:
        pass

    def refresh_display(self) -> None:
        pass


def test_activate_document_matches_ignoring_case() -> None:
    """new_part(name="CaseTest") then save to casetest.CATPart: both work."""
    part = _DocStub("casetest.CATPart")
    tools = DocumentTools(_DocConn([_DocStub("assy.CATProduct"), part]))  # type: ignore[arg-type]
    assert tools._activate_document("CaseTest") == "Activated document: 'casetest.CATPart'"
    assert part.activated


def test_activate_document_prefers_an_exact_match() -> None:
    exact = _DocStub("CaseTest.CATPart")
    other = _DocStub("casetest.CATPart")
    tools = DocumentTools(_DocConn([other, exact]))  # type: ignore[arg-type]
    tools._activate_document("CaseTest")
    assert exact.activated and not other.activated


# ------------------------------------ GSD: a missing element names its document

class _Shapes:
    def __init__(self, names: list[str]) -> None:
        self.names = names
        self.Count = len(names)

    def Item(self, key: Any) -> Any:
        if isinstance(key, int):
            return _Shape(self.names[key - 1])
        raise RuntimeError("no element by that name")


class _Shape:
    def __init__(self, name: str) -> None:
        self.Name = name


class _Collection:
    def __init__(self, items: list[Any]) -> None:
        self.items = items
        self.Count = len(items)

    def Item(self, index: int) -> Any:
        return self.items[index - 1]


class _Geoset:
    def __init__(self, name: str, shapes: list[str]) -> None:
        self.Name = name
        self.HybridShapes = _Shapes(shapes)
        self.HybridSketches = _Shapes([])
        self.HybridBodies = _Collection([])


class _PartStub:
    def __init__(self, shapes: list[str]) -> None:
        self.HybridBodies = _Collection([_Geoset("GSD_Set", shapes)])
        self.Bodies = _Collection([])


class _GSDConn:
    def __init__(self, active: _PartStub, others: dict[str, _PartStub]) -> None:
        self._active = active
        self._docs = [("Part1.CATPart", active)] + list(others.items())

    def ensure_connected(self) -> None:
        pass

    def get_active_part(self) -> Any:
        return self._active

    @property
    def active_document(self) -> Any:
        doc = _DocStub(self._docs[0][0])
        return doc

    @property
    def documents(self) -> Any:
        docs = []
        for name, part in self._docs:
            doc = _DocStub(name)
            doc._part = part  # type: ignore[attr-defined]
            docs.append(doc)
        return _Docs(docs)

    @staticmethod
    def part_of(doc: Any) -> Any:
        return getattr(doc, "_part")


def test_missing_element_names_the_document_that_does_hold_it() -> None:
    tools = GSDTools(_GSDConn(_PartStub(["C1"]), {"Pin.CATPart": _PartStub(["CA"])}))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError) as excinfo:
        tools._find_element("CA")
    message = str(excinfo.value)
    assert "in Pin.CATPart" in message
    assert "catia_activate_document" in message
    assert "'C1'" in message            # what the searched document does hold


def test_missing_element_lists_what_was_searched() -> None:
    tools = GSDTools(_GSDConn(_PartStub(["C1"]), {}))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError) as excinfo:
        tools._find_element("nope")
    message = str(excinfo.value)
    assert "not found in 'Part1.CATPart'" in message
    assert "catia_gsd_list_elements" in message
