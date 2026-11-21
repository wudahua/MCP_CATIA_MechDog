"""0.3.157: the 2026-11-21 report's P1-11 / P1-12 - a repair that fits one call.

The reporter rebuilt two part files that their assembly already referenced. Every
instance kept its pose, none could read PartNumber, reopening did not help, and
the repair cost one call per instance plus one more per instance to get the `.1`
names back - eight instances, sixteen calls.

What was measured on B30 2026-11-21 before any of this was written:

* with an assembly open, the parts it references ARE in Application.Documents
  (``component_of=<assembly>``), and closing one answers "STILL OPEN ... the close
  request was accepted and deferred";
* so ``catia_new_part(overwrite=true)`` over such a file is REFUSED with
  DOCUMENT_ALREADY_OPEN - the warning belongs in that refusal, not after a
  successful overwrite, because that is the only moment a caller is about to do
  it AND the only moment the instance names can still be read;
* after closing the assembly (which takes the part with it), rebuilding, and
  reopening, 4 of 4 instances were broken with their poses intact, and a second
  close/reopen cycle did not mend them;
* one ``catia_replace_component(component_name=[...], keep_name=true)`` call put
  all four back: names restored, poses at 0.0 mm difference, PartNumber readable.

No CATIA here: the stubs stand in for Products/Documents and the sentences are
asserted as data.
"""
from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

import pytest

from catia_mcp.results import ArgumentRejected, CodedError
from catia_mcp.tools.assembly import AssemblyTools, assemblies_referencing
from catia_mcp.tools.document import DocumentTools

POSE = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 10.0, 20.0, 30.0]


# ── doubles ───────────────────────────────────────────────────────────────
class _Ref:
    def __init__(self, path):
        self.Parent = SimpleNamespace(FullName=path)


def _component(name: str, path: str):
    return SimpleNamespace(Name=name, PartNumber=name.rsplit(".", 1)[0],
                           Position=SimpleNamespace(),
                           ReferenceProduct=_Ref(path))


class _StickyName:
    """CATIA accepts ``Name =`` and keeps its own - the case that must be reported."""

    def __init__(self, name, path):
        self._name = name
        self.PartNumber = name.rsplit(".", 1)[0]
        self.Position = SimpleNamespace()
        self.ReferenceProduct = _Ref(path)

    @property
    def Name(self):
        return self._name

    @Name.setter
    def Name(self, value):
        pass


class _Products:
    """A component collection whose insert appends one instance."""

    def __init__(self, components, factory=None):
        self.items = list(components)
        self.Count = len(self.items)
        self.added = 0
        self.factory = factory or (lambda name, path: _component(name, path))

    def Item(self, index):
        if isinstance(index, int):
            return self.items[index - 1]
        for item in self.items:
            if str(item.Name) == str(index):
                return item
        raise KeyError(index)

    def AddComponent(self, path):
        self.added += 1
        # The measured naming: after the old instance is removed, CATIA hands the
        # next insert the SAME free suffix again (all four repairs landed as
        # A1_Piston_v3.1), which is exactly why keep_name exists.
        component = self.factory(f"NEW.{self.added}", path)
        self.items.append(component)
        self.Count = len(self.items)
        return component

    def Remove(self, node):
        self.items.remove(node)
        self.Count = len(self.items)


def _tools(products, doc=None):
    product = SimpleNamespace(Products=products, Name="ASM")
    doc = doc or SimpleNamespace(
        Name="ASM.CATProduct",
        Selection=SimpleNamespace(Clear=lambda: None, Add=lambda node: None))
    conn = SimpleNamespace(
        active_document=doc, ensure_connected=lambda: None,
        refresh_display=lambda: None, get_active_product=lambda: product,
        product_of=lambda document: product,
        part_of=lambda document: (_ for _ in ()).throw(RuntimeError("not a part")),
    )
    tools = AssemblyTools(conn)
    tools._position_components = lambda position: list(POSE)
    tools._add_component = lambda path: products.AddComponent(path)
    tools._set_component_transform = lambda args: "posed"
    return tools


def _file(tmp_path, name="P05.CATPart"):
    path = tmp_path / name
    path.write_text("x", encoding="utf-8")
    return str(path)


# ── the schema ────────────────────────────────────────────────────────────
def test_the_schema_offers_keep_name_and_a_list_of_instances():
    tool = [d for d in AssemblyTools(connection=None).get_tool_definitions()
            if d["name"] == "catia_replace_component"][0]
    properties = tool["inputSchema"]["properties"]
    assert properties["keep_name"]["type"] == "boolean"
    assert properties["keep_name"]["default"] is False
    assert "array" in properties["component_name"]["type"]
    # The single-instance call is unchanged: this is an addition, not a rewrite.
    assert tool["inputSchema"]["required"] == ["component_name", "file_path"]


def test_the_description_tells_a_breaking_caller_what_repairs_it():
    tool = [d for d in AssemblyTools(connection=None).get_tool_definitions()
            if d["name"] == "catia_replace_component"][0]
    text = tool["description"]
    assert "PASS A LIST" in text
    assert "STOPS at the first failure" in text
    assert "keep_name" in text
    # The reason the parameter exists, in the number the report measured.
    assert "P05.1 becomes P05.5" in text


def test_list_components_says_broken_instances_have_a_one_call_repair():
    tool = [d for d in AssemblyTools(connection=None).get_tool_definitions()
            if d["name"] == "catia_list_components"][0]
    text = tool["description"]
    assert "broken_by_part" in text
    assert "ONE call per part" in text


# ── the batch ─────────────────────────────────────────────────────────────
def test_a_batch_takes_the_old_names_back(tmp_path):
    path = _file(tmp_path)
    products = _Products([_component(f"P05.{i}", path) for i in (1, 2, 3)])
    tools = _tools(products)

    reply = json.loads(tools.execute("catia_replace_component",
                                     {"component_name": ["P05.1", "P05.2", "P05.3"],
                                      "file_path": path, "keep_name": True}))
    assert (reply["count"], reply["replaced"], reply["failed"]) == (3, 3, 0)
    assert [c["replaced_by"] for c in reply["components"]] == ["P05.1", "P05.2", "P05.3"]
    assert all(c["name_kept"] for c in reply["components"])
    assert [item.Name for item in products.items] == ["P05.1", "P05.2", "P05.3"]
    assert reply["max_pose_difference_mm"] == 0.0
    assert "Not saved yet" in reply["note"]


def test_without_keep_name_the_name_is_not_taken_back(tmp_path):
    """The control: the defect the parameter fixes, measured in the same call."""
    path = _file(tmp_path)
    products = _Products([_component(f"P05.{i}", path) for i in (1, 2)])
    tools = _tools(products)

    reply = json.loads(tools.execute("catia_replace_component",
                                     {"component_name": ["P05.1", "P05.2"],
                                      "file_path": path}))
    assert all(not c["name_kept"] for c in reply["components"])
    assert [c["replaced_by"] for c in reply["components"]] == ["NEW.1", "NEW.2"]
    assert reply["keep_name"] is False


def test_every_name_is_resolved_before_the_first_insert(tmp_path):
    """A typo in the last entry used to surface after three inserts had landed."""
    path = _file(tmp_path)
    products = _Products([_component(f"P05.{i}", path) for i in (1, 2, 3)])
    tools = _tools(products)

    with pytest.raises(Exception) as caught:  # noqa: B017 - ComponentNotFound
        tools.execute("catia_replace_component",
                      {"component_name": ["P05.1", "P05.2", "Nope.9"],
                       "file_path": path, "keep_name": True})
    assert "Nope.9" in str(caught.value)
    assert products.added == 0
    assert [item.Name for item in products.items] == ["P05.1", "P05.2", "P05.3"]


def test_the_same_name_twice_is_refused_before_anything_changes(tmp_path):
    path = _file(tmp_path)
    products = _Products([_component("P05.1", path)])
    tools = _tools(products)

    with pytest.raises(ArgumentRejected) as caught:
        tools.execute("catia_replace_component",
                      {"component_name": ["P05.1", "P05.1"], "file_path": path})
    assert "lists 'P05.1' twice" in str(caught.value)
    assert products.added == 0


def test_an_empty_list_is_refused_with_the_reason(tmp_path):
    path = _file(tmp_path)
    tools = _tools(_Products([]))
    with pytest.raises(ArgumentRejected) as caught:
        tools.execute("catia_replace_component",
                      {"component_name": [], "file_path": path})
    assert "empty list" in str(caught.value)


def test_a_batch_that_fails_halfway_says_what_landed(tmp_path):
    """Partial success is a failure state: the reply must be refusable by a program."""
    path = _file(tmp_path)
    state = {"inserts": 0}

    def failing(name, path_):
        state["inserts"] += 1
        if state["inserts"] == 2:
            raise RuntimeError("CATIA refused the insert")
        return _component(name, path_)

    products = _Products([_component(f"P05.{i}", path) for i in (1, 2, 3)],
                         factory=failing)
    tools = _tools(products)

    with pytest.raises(CodedError) as caught:
        tools.execute("catia_replace_component",
                      {"component_name": ["P05.1", "P05.2", "P05.3"],
                       "file_path": path, "keep_name": True})
    error = caught.value
    assert error.code == "REPLACE_BATCH_INCOMPLETE"
    assert error.modified is True
    assert error.data["replaced"] == ["P05.1"]
    assert error.data["failed_at"] == "P05.2"
    assert error.data["not_attempted"] == ["P05.3"]
    assert "Replaced 1 of 3" in str(error)
    assert "BOTH instances" in str(error)


def test_a_batch_that_fails_on_the_first_entry_did_not_change_the_model(tmp_path):
    path = _file(tmp_path)

    def failing(name, path_):
        raise ArgumentRejected("that file cannot be inserted")

    products = _Products([_component("P05.1", path)], factory=failing)
    tools = _tools(products)

    with pytest.raises(CodedError) as caught:
        tools.execute("catia_replace_component",
                      {"component_name": ["P05.1"], "file_path": path})
    assert caught.value.code == "REPLACE_BATCH_INCOMPLETE"
    # Nothing landed and the failure says it changed nothing - not "unknown".
    assert caught.value.modified is False


# ── keep_name, single instance ────────────────────────────────────────────
def test_the_single_reply_says_the_name_was_set_back_and_read(tmp_path):
    path = _file(tmp_path)
    products = _Products([_component("P05.1", path)])
    tools = _tools(products)

    reply = tools.execute("catia_replace_component",
                          {"component_name": "P05.1", "file_path": path,
                           "keep_name": True})
    assert "replaced IN PLACE" in reply
    assert "'NEW.1'" in reply and "'P05.1'" in reply
    assert "read back" in reply
    assert "read back, not assumed" in reply          # the reference, still
    assert products.items[0].Name == "P05.1"


def test_a_name_catia_does_not_take_is_reported_not_assumed(tmp_path):
    """A rename that does not stick is data, not a failure of the replace."""
    path = _file(tmp_path)
    products = _Products([_component("P05.1", path)],
                         factory=lambda name, path_: _StickyName("NEW.1", path_))
    tools = _tools(products)

    reply = tools.execute("catia_replace_component",
                          {"component_name": "P05.1", "file_path": path,
                           "keep_name": True})
    assert "was NOT restored" in reply
    assert "CATIA kept 'NEW.1' instead of 'P05.1'" in reply
    assert "catia_rename_component" in reply


# ── list_components: the repair is named where the damage is seen ─────────
class _Broken:
    def __init__(self, name):
        self.Name = name
        self.Position = SimpleNamespace()

    @property
    def PartNumber(self):
        raise RuntimeError("方法 PartNumber 失败")

    @property
    def ReferenceProduct(self):
        raise RuntimeError("方法 ReferenceProduct 失败")


class _Nameless:
    """Broken AND unreadable by name: it cannot be put into a repair call."""

    def __init__(self):
        self.Position = SimpleNamespace()

    @property
    def Name(self):
        raise RuntimeError("方法 Name 失败")

    @property
    def PartNumber(self):
        raise RuntimeError("方法 PartNumber 失败")

    @property
    def ReferenceProduct(self):
        raise RuntimeError("方法 ReferenceProduct 失败")


def test_broken_instances_are_listed_by_part_with_the_call_that_repairs_them():
    products = _Products([_Broken("P05.1"), _Broken("P05.2"), _Broken("P06.1")])
    tools = _tools(products)

    payload = json.loads(tools.execute("catia_list_components", {}))
    assert payload["broken"] == 3
    assert payload["broken_instances"] == ["P05.1", "P05.2", "P06.1"]
    assert payload["broken_by_part"] == {"P05": ["P05.1", "P05.2"],
                                         "P06": ["P06.1"]}
    note = payload["note"]
    assert "catia_replace_component(component_name=['P05.1', 'P05.2']" in note
    assert "keep_name=true" in note
    assert "P05 (2), P06 (1)" in note
    # The old advice sent the caller to catia_add_component, which cannot keep a
    # pose and cannot take a name - it was the wrong tool for this repair.
    assert "catia_add_component" not in note


def test_a_broken_instance_with_no_readable_name_is_counted_not_listed():
    products = _Products([_Broken("P05.1"), _Nameless()])
    tools = _tools(products)

    payload = json.loads(tools.execute("catia_list_components", {}))
    assert payload["broken"] == 2
    assert payload["broken_by_part"] == {"P05": ["P05.1"]}
    assert "1 further broken instance(s) could not even be named" in payload["note"]


def test_a_healthy_listing_carries_none_of_the_repair_fields(tmp_path):
    path = _file(tmp_path)
    products = _Products([_component("P05.1", path)])
    tools = _tools(products)

    payload = json.loads(tools.execute("catia_list_components", {}))
    assert "broken" not in payload
    assert "broken_by_part" not in payload


# ── the scanner, and the one place the document type is decided ───────────
class _Docs:
    def __init__(self, documents):
        self.items = list(documents)
        self.Count = len(self.items)

    def Item(self, index):
        return self.items[index - 1]


def _product_doc(name, components):
    return SimpleNamespace(
        Name=name, Type="Product",
        Product=SimpleNamespace(Products=_Products(components)))


def test_the_scanner_matches_the_file_not_the_name(tmp_path):
    """Case and separator differences are the same file; a copy is not."""
    target = tmp_path / "P05.CATPart"
    target.write_text("x", encoding="utf-8")
    same = str(target).replace("\\", "/").upper()
    other = str(tmp_path / "P05_copy.CATPart")

    documents = _Docs([
        _product_doc("ASM1.CATProduct",
                     [_component("P05.1", same), _component("P05.2", same),
                      _component("P09.1", other)]),
        # A PartDocument answers .Product too - it must not be walked as one.
        SimpleNamespace(Name="P05.CATPart", Type="Part",
                        Product=SimpleNamespace(Products=_Products([]))),
    ])
    found = assemblies_referencing(str(target), documents)
    assert [item["document"] for item in found] == ["ASM1.CATProduct"]
    assert found[0]["instances"] == ["P05.1", "P05.2"]
    assert found[0]["unreadable"] == 0


def test_the_scanner_counts_a_reference_it_cannot_read():
    """A broken instance cannot be matched to a file - it is counted, not guessed."""
    documents = _Docs([_product_doc("ASM1.CATProduct",
                                    [_Broken("P05.1"), _component("P05.2", "D:/x/P05.CATPart")])])
    found = assemblies_referencing("D:/y/P05.CATPart", documents)
    assert found == [{"document": "ASM1.CATProduct", "instances": [],
                      "unreadable": 1}]


def test_the_document_type_check_is_not_copied_into_the_document_tools():
    path = pathlib.Path(DocumentTools.__module__.replace(".", "/") + ".py")
    text = (pathlib.Path(__file__).resolve().parent.parent / path).read_text(
        encoding="utf-8")
    assert "assemblies_referencing" in text
    # One place decides what an assembly is. A fourth copy is a fourth opinion.
    assert '"PRODUCT" in' not in text


# ── the warning: same facts, two tenses ───────────────────────────────────
def _fact():
    return {"total": 4, "unreadable": 0,
            "assemblies": [{"document": "A1_Asm.CATProduct",
                            "instances": ["A1_Piston.1", "A1_Piston.2",
                                          "A1_Piston.3", "A1_Piston.4"],
                            "unreadable": 0}]}


def test_the_refusal_speaks_in_the_conditional():
    text = DocumentTools(connection=None)._reference_warning(
        "D:/parts/P05.CATPart", _fact(), occurred=False)
    assert "4 instance(s) in 1 open assembly document(s)" in text
    assert "would keep" in text and "would throw" in text
    assert "keep_name=true" in text
    assert "does NOT repair them" in text


def test_the_successful_rebuild_speaks_in_the_present():
    text = DocumentTools(connection=None)._reference_warning(
        "D:/parts/P05.CATPart", _fact(), occurred=True)
    assert "would keep" not in text
    assert "keep their poses and throw" in text
    assert "D:/parts/P05.CATPart" in text


def test_a_broken_only_match_never_reads_as_no_instances():
    """'referenced by 0 instance(s)' is what an all-broken match would print."""
    fact = {"total": 0, "unreadable": 4,
            "assemblies": [{"document": "A1_Asm.CATProduct", "instances": [],
                            "unreadable": 4}]}
    text = DocumentTools(connection=None)._reference_warning(
        "D:/parts/P05.CATPart", fact, occurred=True)
    assert "0 instance(s)" not in text
    assert "4 instance(s)" in text
    assert "already broken" in text
    # The names cannot be "above" - they have to be looked up.
    assert "catia_list_components lists" in text


def test_no_reference_means_no_extra_sentence():
    assert DocumentTools(connection=None)._reference_warning(
        "D:/parts/P05.CATPart", None, occurred=True) == ""


# ── the warning sits in the refusal, because that is where it can fire ────
def test_new_part_names_the_instances_it_would_break(tmp_path):
    target = tmp_path / "P05.CATPart"
    target.write_text("x", encoding="utf-8")
    holder = SimpleNamespace(Name="P05.CATPart", FullName=str(target))
    documents = _Docs([holder, _product_doc(
        "ASM1.CATProduct", [_component("P05.1", str(target)),
                            _component("P05.2", str(target))])])
    conn = SimpleNamespace(documents=documents, ensure_connected=lambda: None)
    tools = DocumentTools(conn)

    with pytest.raises(CodedError) as caught:
        tools.execute("catia_new_part", {"name": "P05v2", "file_path": str(target),
                                         "overwrite": True})
    error = caught.value
    assert error.code == "DOCUMENT_ALREADY_OPEN"
    message = str(error)
    assert "'P05.1', 'P05.2'" in message
    assert "ASM1.CATProduct" in message
    assert "would keep their poses" in message
    assert "keep_name=true" in message
    # A machine can act on the same facts without parsing the sentence.
    assert error.data["referenced_by"]["total"] == 2
    assert any("close the assembly first" in action
               for action in error.next_actions)


def test_new_part_says_nothing_extra_when_no_assembly_uses_the_file(tmp_path):
    target = tmp_path / "P05.CATPart"
    target.write_text("x", encoding="utf-8")
    holder = SimpleNamespace(Name="P05.CATPart", FullName=str(target))
    tools = DocumentTools(SimpleNamespace(documents=_Docs([holder]),
                                          ensure_connected=lambda: None))

    with pytest.raises(CodedError) as caught:
        tools.execute("catia_new_part", {"name": "P05v2", "file_path": str(target),
                                         "overwrite": True})
    assert "pointed at by" not in str(caught.value)
    assert "referenced_by" not in caught.value.data
