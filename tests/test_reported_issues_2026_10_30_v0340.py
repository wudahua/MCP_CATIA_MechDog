"""0.3.41 - report 2026-10-30: the fillet that failed only after a GSD write.

The report's §1 said `catia_fillet(edge_anchors=...)` fails with E_FAIL in any
document that has seen a GSD write, while `edge_name` still works, and that it
survives deleting the GSD elements and reopening the file. The 0.3.40 note
recorded the opposite ("not reproduced") - that measurement used a fresh
document, which is precisely the case that works.

Reproduced on B30 2026-10-30 by adding one `catia_gsd_point` to the same box:

    before the point      anchors CREATED
    after the point       anchors REFUSED (E_FAIL)   name CREATED
    after deleting it     anchors REFUSED            name CREATED

The cause is not the edge reference: adding a HybridBody moves CATIA's
InWorkObject onto the geometrical set, and the scratch probe set that the anchor
path creates for its distance measurements does exactly that. Deleting the probe
set does not move it back, so the Part Design factory is called with a
geometrical set in work and answers a bare E_FAIL. Setting InWorkObject back to
the body makes the same reference build the fillet immediately - measured, one
line apart.

Two defects are asserted here: that restore, and a rollback path that raised
`NameError: name 'body' is not defined` instead of reporting the rejection.
"""

import pytest


class _Set:
    def __init__(self, name):
        self.Name = name

    def AppendHybridShape(self, shape):
        pass


class _Sets:
    """CATIA's HybridBodies: adding one moves the in-work object onto it."""

    def __init__(self, part=None):
        self.part = part
        self.items = []
        self.Count = 0

    def Add(self):
        item = _Set("MCP_probe_points")
        self.items.append(item)
        self.Count = len(self.items)
        if self.part is not None:
            self.part.InWorkObject = item
        return item

    def Item(self, index):
        return self.items[index - 1]


class _Selection:
    def Clear(self):
        pass

    def Add(self, item):
        pass

    def Delete(self):
        pass


class _Body:
    def __init__(self, name="零件几何体"):
        self.Name = name


class _Factory:
    def AddNewPointCoord(self, x, y, z):
        return _Set(f"点.{x}")


class _Part:
    """A part whose in-work object follows HybridBodies.Add, like CATIA's."""

    def __init__(self):
        self.Bodies = type("B", (), {"Count": 1, "Item": staticmethod(lambda i: _Body())})()
        self.HybridBodies = _Sets(self)
        self.HybridShapeFactory = _Factory()
        self._in_work = _Body()
        self.updates = 0

    @property
    def InWorkObject(self):
        return self._in_work

    @InWorkObject.setter
    def InWorkObject(self, value):
        self._in_work = value

    def Update(self):
        self.updates += 1


def _part_design(part):
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    tools.conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "get_active_part": staticmethod(lambda: part),
        "get_active_part_body": staticmethod(lambda: _Body()),
        "refresh_display": staticmethod(lambda: None),
        "hso": _Selection(),
    })()
    return tools


# ─────────────── 1. the probe set hands the body back
def test_the_probe_set_restores_the_in_work_object():
    from catia_mcp.tools.part_design import _ProbePoints

    part = _Part()
    body = _Body()
    part.InWorkObject = body
    probe = _ProbePoints(type("C", (), {"hso": _Selection()})(), part)
    # Adding the scratch set moved the in-work object onto it, which is what
    # CATIA does and what made every later fillet fail.
    assert part.InWorkObject is probe.set
    probe.close()
    assert part.InWorkObject is body


def test_the_probe_set_leaves_a_foreign_in_work_object_alone():
    """A caller that deliberately put a datum plane in work gets it back."""
    from catia_mcp.tools.part_design import _ProbePoints

    part = _Part()
    datum = _Body("Datum_xy")
    part.InWorkObject = datum
    probe = _ProbePoints(type("C", (), {"hso": _Selection()})(), part)
    probe.close()
    assert part.InWorkObject is datum


# ─────────────── 2. a rejected feature reports the rejection, not a NameError
class _RejectingPart(_Part):
    def __init__(self):
        super().__init__()
        self.removed = []

    def UpdateObject(self, feature):
        raise RuntimeError("方法 UpdateObject 失败")


def test_a_rejected_single_feature_rolls_back_instead_of_raising_name_error():
    from catia_mcp.tools.part_design import FeatureRejected

    part = _RejectingPart()
    body = _Body()
    tools = _part_design(part)
    tools._remove_feature = lambda p, f, b=None: part.removed.append(f) or True
    feature = type("F", (), {"Name": "倒圆角.1"})()

    with pytest.raises(FeatureRejected) as error:
        tools._safe_update(part, feature, body)
    assert "rolled_back: true" in str(error.value)
    assert part.removed == [feature]


def test_the_rollback_puts_the_body_back_in_work():
    from catia_mcp.tools.part_design import FeatureRejected

    part = _RejectingPart()
    part.InWorkObject = _Body("GSD_Set")
    tools = _part_design(part)

    def remove(part_arg, feature, body=None):
        # what Selection.Delete + Update leaves behind on B30: still the set
        assert body is not None, "the body has to reach the rollback"
        return True

    tools._remove_feature = remove
    with pytest.raises(FeatureRejected):
        tools._safe_update(part, type("F", (), {"Name": "倒圆角.1"})(), _Body())
