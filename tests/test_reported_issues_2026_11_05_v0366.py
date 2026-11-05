"""Sheet furniture in Chinese by default (0.3.66).

The user asked why the drawing came out covered in English. The answer was
not CATIA: the labels were hard-coded in ``_add_frame`` and every default
view name was English too, while CATIA's own drafting font (SICH) draws
Chinese perfectly well - verified on B30 by writing 材料/图号/比例/制图/审核
into table cells, exporting a PDF and looking at it
(``scripts/b30_v0366_zh_font.py``).

What these tests pin:

  1. The default is Chinese, and ``language='en'`` still produces the ISO
     wording, so nothing that was working is taken away.
  2. ``labels`` overrides a single field without forcing the whole sheet into
     the other language (a Chinese sheet may still say "SHEET NO.").
  3. An unknown language or label key is refused BEFORE the border is drawn -
     the same rule the block size follows: a refused call leaves the sheet
     exactly as it was.
  4. The default view names are Chinese, because a view's Name is printed on
     the sheet.
"""

from __future__ import annotations

import pytest

from catia_mcp.tools.drawing import DraftingTools


# ── stubs ────────────────────────────────────────────────────────────────────


class _CellText:
    def SetFontSize(self, first, count, size):
        return None


class _Table:
    def __init__(self, x, y, rows, columns) -> None:
        self.NumberOfRows = rows
        self.NumberOfColumns = columns
        self.cells: dict[tuple[int, int], str] = {}

    def SetColumnSize(self, index, size):
        return None

    def SetRowSize(self, index, size):
        return None

    def MergeCells(self, *args):
        return None

    def SetCellString(self, row, column, value):
        self.cells[(row, column)] = value

    def GetCellString(self, row, column):
        return self.cells.get((row, column), "")

    def SetCellAlignment(self, row, column, align):
        return None

    def GetCellObject(self, row, column):
        return _CellText()


class _Tables:
    def __init__(self) -> None:
        self.items: list[_Table] = []

    @property
    def Count(self) -> int:
        return len(self.items)

    def Add(self, x, y, rows, columns, row_height, column_width):
        table = _Table(x, y, rows, columns)
        self.items.append(table)
        return table


class _Text:
    def __init__(self, content) -> None:
        self.Text = content
        self.AnchorPosition = None

    def SetFontSize(self, first, count, size):
        return None


class _Texts:
    def __init__(self) -> None:
        self.items: list[_Text] = []

    @property
    def Count(self) -> int:
        return len(self.items)

    def Add(self, content, x, y):
        text = _Text(content)
        self.items.append(text)
        return text


class _Factory:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def CreateLine(self, a, b, c, d):
        self.calls.append((a, b, c, d))


class _Elements:
    def __init__(self, factory: _Factory) -> None:
        self.factory = factory

    @property
    def Count(self) -> int:
        return 1 + len(self.factory.calls)


class _Background:
    Name = "Background View"

    def __init__(self) -> None:
        self.Factory2D = _Factory()
        self.GeometricElements = _Elements(self.Factory2D)
        self.Tables = _Tables()
        self.Texts = _Texts()

    def Activate(self):
        return None


class _Views:
    def __init__(self, background: _Background) -> None:
        self.items = [object(), background]

    @property
    def Count(self) -> int:
        return len(self.items)

    def Item(self, index):
        return self.items[index - 1]


class _Sheet:
    PaperSize = 5
    Orientation = 1
    Scale = 1.0
    ProjectionMethod = 1
    Name = "图纸.1"

    def __init__(self) -> None:
        self.background = _Background()
        self.Views = _Views(self.background)

    def GetPaperWidth(self):
        return 420.0

    def GetPaperHeight(self):
        return 297.0


class _Conn:
    def __init__(self, sheet: _Sheet) -> None:
        self.drawing = type("D", (), {"Sheets": type("S", (), {"ActiveSheet": sheet})(),
                                      "Activate": lambda self: None})()
        self.documents = None
        self.last_part_name = None
        self.app = None

    def ensure_connected(self):
        return None

    def refresh_display(self):
        return None


def _tools(sheet: _Sheet) -> DraftingTools:
    tools = DraftingTools(_Conn(sheet))
    tools._find_active_drawing = lambda: tools.conn.drawing  # type: ignore[assignment]
    return tools


def _frame(sheet: _Sheet, **overrides):
    tools = _tools(sheet)
    reply = tools.execute("catia_drawing_add_frame",
                          {"paper": "A3", "title_block": {"material": "PET"},
                           **overrides})
    return tools, sheet.background.Tables.items[0], reply


# ── labels ──────────────────────────────────────────────────────────────────


def test_the_six_field_labels_are_chinese_by_default():
    _, table, _ = _frame(_Sheet())
    assert (table.cells[(2, 1)], table.cells[(2, 5)]) == ("材料", "比例")
    assert (table.cells[(3, 1)], table.cells[(3, 5)]) == ("制图", "日期")
    assert (table.cells[(4, 1)], table.cells[(4, 5)]) == ("审核", "张次")


def test_english_is_one_argument_away():
    _, table, _ = _frame(_Sheet(), language="en")
    assert (table.cells[(2, 1)], table.cells[(3, 1)]) == ("MATERIAL", "DRAWN")
    assert table.cells[(4, 5)] == "SHEET"


@pytest.mark.parametrize("alias", ["zh-CN", "CN", "中文", "chinese"])
def test_language_aliases_reach_chinese(alias):
    _, table, _ = _frame(_Sheet(), language=alias)
    assert table.cells[(2, 1)] == "材料"


def test_a_single_label_can_be_overridden():
    _, table, _ = _frame(_Sheet(), labels={"checked": "校对", "sheet": "SHEET NO."})
    assert table.cells[(4, 1)] == "校对"
    assert table.cells[(4, 5)] == "SHEET NO."
    assert table.cells[(2, 1)] == "材料", "the rest of the block stays Chinese"


def test_labels_win_over_language():
    _, table, _ = _frame(_Sheet(), language="en", labels={"material": "材质"})
    assert table.cells[(2, 1)] == "材质"
    assert table.cells[(2, 5)] == "SCALE"


def test_an_unknown_language_is_refused_before_the_border_is_drawn():
    sheet = _Sheet()
    tools = _tools(sheet)
    with pytest.raises(ValueError) as caught:
        tools.execute("catia_drawing_add_frame",
                      {"paper": "A3", "language": "fr"})
    assert "Unknown language" in str(caught.value)
    assert "zh" in str(caught.value) and "en" in str(caught.value)
    assert sheet.background.Factory2D.calls == [], (
        "a refused frame must not leave stray lines - the same rule the "
        "oversized block follows")
    assert sheet.background.Tables.Count == 0


def test_an_unknown_label_key_is_refused_and_names_the_ones_it_has():
    sheet = _Sheet()
    tools = _tools(sheet)
    with pytest.raises(ValueError) as caught:
        tools.execute("catia_drawing_add_frame",
                      {"paper": "A3", "labels": {"tolerance": "公差"}})
    message = str(caught.value)
    assert "tolerance" in message
    for key in ("material", "scale", "drawn", "date", "checked", "sheet"):
        assert key in message
    assert sheet.background.Factory2D.calls == []
    assert sheet.background.Tables.Count == 0


def test_the_reply_says_which_language_was_used():
    _, _, reply = _frame(_Sheet())
    assert "中文" in reply or "zh" in reply, (
        "the caller has to be able to tell, from the reply alone, what "
        "language the block came out in")


# ── default view names ──────────────────────────────────────────────────────


class _GenBehavior:
    def __init__(self) -> None:
        self.Document = object()
        self.calls: list[str] = []

    def Update(self) -> None:
        return None

    def __getattr__(self, name):
        # DefineProjectionView / DefineSectionView / DefineCircularDetailView
        # all live here; the test only cares about the NAME the view gets.
        if name.startswith("__"):
            raise AttributeError(name)

        def record(*args, **kwargs):
            self.calls.append(name)
            return None

        return record


class _View:
    def __init__(self, name) -> None:
        self.Name = name
        self.x = 0.0
        self.y = 0.0
        self.Scale = 1.0
        self.GenerativeBehavior = _GenBehavior()


def _view_tools(monkeypatch):
    sheet = _Sheet()
    tools = _tools(sheet)
    captured: dict[str, str] = {}

    parent = _View("父视图")

    def fake_parent(name=None):
        return parent

    def fake_create(sheet_arg, parent_arg, name, define):
        captured["name"] = name
        view = _View(name)
        define(view)
        return view

    tools._parent_generative_view = fake_parent            # type: ignore[assignment]
    tools._create_derived_view = fake_create               # type: ignore[assignment]
    tools._place = lambda view, x, y, scale=None: scale    # type: ignore[assignment]
    tools.conn.drawing.Sheets.ActiveSheet.Views.Add = None  # not used any more
    return tools, captured


@pytest.mark.parametrize("direction,expected", [
    ("right", "右视图"), ("left", "左视图"), ("top", "俯视图"),
    ("bottom", "仰视图"), ("rear", "后视图"),
])
def test_projection_views_are_named_in_chinese(monkeypatch, direction, expected):
    tools, captured = _view_tools(monkeypatch)
    tools.execute("catia_drawing_projection_view", {"direction": direction})
    assert captured["name"] == expected


def test_section_and_detail_defaults_are_chinese(monkeypatch):
    tools, captured = _view_tools(monkeypatch)
    tools.execute("catia_drawing_section_view",
                  {"profile": [0, 0, 10, 0, 10, 10, 0, 10]})
    assert captured["name"] == "剖视图"
    tools.execute("catia_drawing_detail_view",
                  {"center_x": 1, "center_y": 1, "radius": 5})
    assert captured["name"] == "局部视图"
