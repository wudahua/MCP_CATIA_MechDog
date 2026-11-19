"""2026-11-17 report: four of its six items are pinned here.

The M113 build report is long and its findings are mixed - two are geometry bugs
(`catia_mirror` undoing a cut, fillet batches that stop at 2/4), two are usability
(close_document needs the extension, run_plan loses its trace on a failed
assertion), and one is a capability question (`catia_sketch_spline` has no control
points) that is answered elsewhere.

What is pinned here, all without CATIA:

* the mirror REFUSES a cut seed, and says why in terms of the measurement that
  showed the old behaviour was wrong (`Mirror.Object` is read-only; the whole-body
  operation unions the image, which fills a cut);
* `catia_close_document` resolves a stem, like `catia_activate_document` always
  did - the same four tiers, still refusing ambiguity;
* a plan whose `expect` fails keeps its step records under `summary=true`, while a
  plan that passes still trims them to nothing;
* the anchor path's retry re-matches the anchors instead of handing it the
  references that the failed batch just invalidated, and an anchor miss carries
  its candidates as data.
"""
from __future__ import annotations

import inspect

import pytest

from catia_mcp.results import CodedError
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.part_design import PartDesignTools


# ── 1. the mirror ──────────────────────────────────────────────────────────
def _mirror_description() -> str:
    from catia_mcp.tools.part_design import PartDesignTools as P

    tools = {tool["name"]: tool for tool in P.get_tool_definitions(P(connection=None))}
    return tools["catia_mirror"]["description"]


def test_the_mirror_tool_no_longer_promises_a_feature_mirror():
    text = _mirror_description()
    assert "MIRROR THE WHOLE BODY" in text
    assert "can not be set" in text, "the read-only seed is the whole reason"
    assert "MIRROR_WOULD_FILL_CUT" in text
    assert "MIRROR_SEED_UNSUPPORTED" in text
    # the numbers the refusal and the description both quote
    assert "26228730.962" in text and "42188262.4593" in text


def test_the_mirror_offers_both_parameters():
    from catia_mcp.tools.part_design import PartDesignTools as P

    tools = {tool["name"]: tool for tool in P.get_tool_definitions(P(connection=None))}
    properties = tools["catia_mirror"]["inputSchema"]["properties"]
    assert "feature" in properties
    assert "allow_body_mirror" in properties
    assert properties["allow_body_mirror"].get("default") is False


def test_the_cut_kinds_the_refusal_is_based_on():
    """A pocket is the measured case; the rest are the same fact."""
    assert set(PartDesignTools._CUT_KINDS) >= {
        "pocket", "groove", "hole", "split", "shell", "thickness"}


def test_the_mirror_refuses_before_it_creates_anything():
    source = inspect.getsource(PartDesignTools._mirror)
    refuse = source.index("MIRROR_WOULD_FILL_CUT")
    create = source.index("sf.AddNewMirror(")
    assert refuse < create, "the refusal has to happen before the feature exists"


# ── 2. close_document by stem ──────────────────────────────────────────────
class _Doc:
    def __init__(self, name: str, full: str = ""):
        self.Name = name
        self.FullName = full


class _Documents:
    def __init__(self, docs):
        self._docs = docs

    @property
    def Count(self):
        return len(self._docs)

    def Item(self, index):
        return self._docs[index - 1]


class _Connection:
    def __init__(self, docs):
        self.documents = _Documents(docs)
        self.active_document = docs[0]


def test_a_document_is_found_by_its_stem():
    docs = [_Doc("Part1.CATPart", r"C:\work\Part1.CATPart"),
            _Doc("P05.CATPart", r"C:\work\P05.CATPart")]
    tools = DocumentTools(connection=_Connection(docs))
    assert tools._locate_document("Part1") is docs[0]
    assert tools._locate_document("p05") is docs[1], "case-insensitive too"
    assert tools._locate_document("Part1.CATPart") is docs[0]
    assert tools._locate_document(r"C:\work\P05.CATPart") is docs[1]


def test_an_unknown_name_still_lists_what_is_open():
    docs = [_Doc("Part1.CATPart", r"C:\work\Part1.CATPart")]
    tools = DocumentTools(connection=_Connection(docs))
    with pytest.raises(CodedError) as caught:
        tools._locate_document("Missing")
    assert caught.value.code == "DOCUMENT_NOT_FOUND"
    assert "Part1.CATPart" in str(caught.value)


def test_two_documents_with_one_stem_are_still_refused():
    """A stem is a weaker identity than a name, so it must not start guessing."""
    docs = [_Doc("Plate.CATPart", r"C:\a\Plate.CATPart"),
            _Doc("Plate.CATPart", r"C:\b\Plate.CATPart")]
    tools = DocumentTools(connection=_Connection(docs))
    with pytest.raises(CodedError) as caught:
        tools._locate_document("Plate")
    assert caught.value.code == "DOCUMENT_AMBIGUOUS"
    assert "C:\\a\\Plate.CATPart" in str(caught.value)


# ── 3. run_plan keeps its trace when the ASSERTION fails ──────────────────
class _Module:
    def execute(self, name, arguments):
        return "built"


def _planner(measurements):
    from catia_mcp.tools.planner import PlannerTools

    planner = PlannerTools(connection=None)
    planner.bind({"catia_sketch_arc": _Module()})
    planner._schemas = {"catia_sketch_arc": {"properties": {}}}
    planner._measure_part = lambda: dict(measurements)
    return planner


_PLAN = {"steps": [{"tool": "catia_sketch_arc", "args": {}},
                   {"tool": "catia_sketch_arc", "args": {}},
                   {"tool": "catia_sketch_arc", "args": {}}],
         "summary": True}


def test_a_failed_assertion_keeps_every_step_record():
    planner = _planner({"volume_mm3": 100.0, "faces": 6, "edges": 12, "vertices": 8})
    reply = planner._run_plan({**_PLAN, "expect": {"volume_mm3": 999.0}})
    data = reply["data"]
    assert reply["code"] == "ASSERTION_FAILED"
    assert len(data["steps"]) == 3, "the trace is the only way to find the culprit"
    assert data["steps_omitted"] == 0
    assert "no STEP failed" in data["summary_note"]


def test_the_kept_records_are_compact_not_whole_replies():
    """0.3.150: the trace stays, the prose does not.

    The 2026-11-19 report got 19 full records (about 9 KB) on the plan that
    needed a short answer - an assertion had failed. Each kept record is now
    index/tool/code/metrics plus the first 160 characters of its result, which
    still locates the step that moved the number.
    """
    planner = _planner({"volume_mm3": 100.0, "faces": 6, "edges": 12, "vertices": 8})
    reply = planner._run_plan({**_PLAN, "expect": {"volume_mm3": 999.0}})
    steps = reply["data"]["steps"]
    assert len(steps) == 3
    for entry in steps:
        assert set(entry) == {"index", "tool", "ok", "code", "metrics", "result"}
        assert len(entry["result"]) <= 160
    assert "COMPACT" in reply["data"]["summary_note"]


def test_a_passing_plan_still_trims_its_steps():
    planner = _planner({"volume_mm3": 100.0, "faces": 6, "edges": 12, "vertices": 8})
    reply = planner._run_plan({**_PLAN, "expect": {"volume_mm3": 100.0}})
    assert reply["code"] == "PLAN_OK"
    assert reply["data"]["steps"] == []
    assert reply["data"]["steps_omitted"] == 3


def test_a_failed_step_still_trims_to_the_failures():
    class _Bad:
        def execute(self, name, arguments):
            raise CodedError("nope", code="STEP_EXCEPTION")

    from catia_mcp.tools.planner import PlannerTools

    planner = PlannerTools(connection=None)
    planner.bind({"catia_sketch_arc": _Bad()})
    planner._schemas = {"catia_sketch_arc": {"properties": {}}}
    planner._measure_part = lambda: {}
    reply = planner._run_plan({"steps": [{"tool": "catia_sketch_arc", "args": {}},
                                        {"tool": "catia_sketch_arc", "args": {}}],
                               "summary": True, "stop_on_error": True})
    assert reply["code"] == "STEP_FAILED"
    assert len(reply["data"]["steps"]) == 1


# ── 4. the anchor retry re-matches instead of re-using dead references ────
def test_the_anchor_retry_matches_again():
    tools = PartDesignTools(connection=None)
    calls = []

    def fake_match(body, anchors, tol, on_miss="fail"):
        calls.append((list(anchors), tol, on_miss))
        return (["Edge.9 (by anchor)"], ["fresh-ref"], "report", [])

    tools._anchors_to_edges = fake_match          # type: ignore[assignment]
    labels, refs = tools._re_resolve_anchors(
        object(), [[0.0, 0.0, 0.0]], 3.0, (["Edge.1 (by anchor)"], ["stale-ref"]))
    assert calls == [([[0.0, 0.0, 0.0]], 3.0, "skip")]
    assert refs == ["fresh-ref"], "the retry must not walk on dead references"
    assert labels == ["Edge.9 (by anchor)"]


def test_the_anchor_retry_falls_back_instead_of_raising():
    """A retry is not the place to refuse a batch: the per-edge failures are
    already reported, and a refusal here would escape the retry loop."""
    tools = PartDesignTools(connection=None)

    def refuses(*_args, **_kwargs):
        raise CodedError("no edges matched", code="ANCHORS_NOT_MATCHED")

    tools._anchors_to_edges = refuses            # type: ignore[assignment]
    fallback = (["Edge.1 (by anchor)"], ["stale-ref"])
    assert tools._re_resolve_anchors(object(), [[0.0, 0.0, 0.0]], 1.0, fallback) \
        == fallback


def test_the_anchor_path_does_not_hand_the_retry_its_own_labels():
    source = inspect.getsource(PartDesignTools._edges_by_anchor)
    # the comment quotes the old line, so look at the call itself
    assert "resolve=lambda: (labels, refs)" not in source
    assert "resolve=lambda: self._re_resolve_anchors(" in source


def test_an_anchor_miss_carries_candidates_as_data():
    source = inspect.getsource(PartDesignTools._anchors_to_edges)
    assert 'code="ANCHORS_NOT_MATCHED"' in source
    assert '"missed": missed' in source
    assert '"ambiguous": ambiguous' in source
    assert "nearest_edge" in inspect.getsource(PartDesignTools._anchor_trouble_text) \
        or "nearest_edge" in inspect.getsource(PartDesignTools._anchors_to_edges)
