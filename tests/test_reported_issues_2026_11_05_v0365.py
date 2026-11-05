"""The frame and title block, pinned to what CATIA actually did on B30.

Three of these tests exist because the first implementation was wrong in ways
that returned cleanly:

  1. Tables.Add anchors the TOP-left corner and grows downward. Passing the
     bottom margin drew the whole title block below the sheet, and the call still
     printed "title block ... at (230, 10)".
  2. The block-size check ran AFTER the border was drawn, so a refused call left
     four stray lines behind (Background elements 5 -> 9).
  3. GetPaperWidth on a catPaperUser sheet raises. A new drawing IS catPaperUser,
     so the width read cannot be unguarded - and the refusal has to explain the
     paper size rather than printing a COM error.
"""

from __future__ import annotations

import pytest

from catia_mcp.tools.drawing import DraftingTools


# ── stubs ────────────────────────────────────────────────────────────────────


class _Line:
    def __init__(self, coords) -> None:
        self.coords = coords


class _Factory:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple] = []
        self.fail = fail

    def CreateLine(self, a, b, c, d):
        if self.fail:
            raise RuntimeError("(-2147352567, '发生意外。', '方法 CreateLine 失败')")
        self.calls.append((a, b, c, d))
        return _Line((a, b, c, d))


class _CellText:
    def __init__(self) -> None:
        self.sizes: list[float] = []

    def SetFontSize(self, first, count, size):
        self.sizes.append(size)

    def SetFontName(self, first, count, name):
        return None


class _Table:
    def __init__(self, x, y, rows, columns) -> None:
        self.x = x
        self.y = y
        self.NumberOfRows = rows
        self.NumberOfColumns = columns
        self.merges: list[tuple] = []
        self.cells: dict[tuple[int, int], str] = {}
        self.alignments: list[tuple] = []
        self.row_sizes: dict[int, float] = {}
        self.column_sizes: dict[int, float] = {}
        self.fail_merge = False

    def SetColumnSize(self, index, size):
        self.column_sizes[index] = size

    def SetRowSize(self, index, size):
        self.row_sizes[index] = size

    def MergeCells(self, first_row, first_column, rows, columns):
        if self.fail_merge:
            raise RuntimeError("(-2147352567, '发生意外。', '方法 MergeCells 失败')")
        self.merges.append((first_row, first_column, rows, columns))

    def SetCellString(self, row, column, value):
        self.cells[(row, column)] = value

    def GetCellString(self, row, column):
        return self.cells.get((row, column), "")

    def SetCellAlignment(self, row, column, align):
        self.alignments.append((row, column, align))

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

    def Item(self, index):
        return self.items[index - 1]


class _Text:
    def __init__(self, content, x, y) -> None:
        self.Name = f"Text {content[:6]}"
        self.Text = content
        self.x = x
        self.y = y
        self.AnchorPosition = None
        self.sizes: list[float] = []

    def SetFontSize(self, first, count, size):
        self.sizes.append(size)


class _Texts:
    def __init__(self) -> None:
        self.items: list[_Text] = []

    @property
    def Count(self) -> int:
        return len(self.items)

    def Add(self, content, x, y):
        text = _Text(content, x, y)
        self.items.append(text)
        return text


class _Elements:
    def __init__(self, factory: _Factory) -> None:
        self.factory = factory
        self.axis = 1

    @property
    def Count(self) -> int:
        return self.axis + len(self.factory.calls)


class _BackgroundView:
    def __init__(self, fail_lines: bool = False) -> None:
        self.Name = "Background View"
        self.Factory2D = _Factory(fail=fail_lines)
        self.GeometricElements = _Elements(self.Factory2D)
        self.Tables = _Tables()
        self.Texts = _Texts()
        self.activated = False

    def Activate(self):
        self.activated = True


class _Views:
    def __init__(self, background: _BackgroundView) -> None:
        self.background = background
        self.items = [object(), background]

    @property
    def Count(self) -> int:
        return len(self.items)

    def Item(self, index):
        return self.items[index - 1]


class _Sheet:
    def __init__(self, *, paper_size=13, width=420.0, height=297.0,
                 orientation=1, no_width=False, views: int = 2,
                 fail_lines: bool = False, fail_merge: bool = False) -> None:
        self.Name = "图纸.1"
        self.PaperSize = paper_size
        self.Orientation = orientation
        self.PaperName = "A3 ISO" if paper_size == 5 else "User"
        self.Scale = 1.0
        self._width = width
        self._height = height
        self._no_width = no_width
        self.background = _BackgroundView(fail_lines=fail_lines)
        self.background.Tables.Add = self.background.Tables.Add  # keep type
        self.Views = _Views(self.background)
        if views < 2:
            self.Views.items = [object()]
        if fail_merge:
            self.background.Tables.Add = self._add_failing_table  # type: ignore[assignment]

    def _add_failing_table(self, x, y, rows, columns, row_height, column_width):
        table = _Table(x, y, rows, columns)
        table.fail_merge = True
        self.background.Tables.items.append(table)
        return table

    def GetPaperWidth(self):
        if self._no_width:
            raise RuntimeError(
                "(-2147352567, '发生意外。', (0, 'CATIADrawingSheet', "
                "'方法 GetPaperWidth 失败', None, 0, -2147469357), None)")
        return self._width

    def GetPaperHeight(self):
        if self._no_width:
            raise RuntimeError("方法 GetPaperHeight 失败")
        return self._height


class _Sheets:
    def __init__(self, sheet: _Sheet) -> None:
        self.ActiveSheet = sheet

    @property
    def Count(self) -> int:
        return 1


class _Drawing:
    def __init__(self, sheet: _Sheet) -> None:
        self.Name = "Drawing1.CATDrawing"
        self.Sheets = _Sheets(sheet)

    def Activate(self):
        return None


class _Conn:
    def __init__(self, sheet: _Sheet) -> None:
        self.drawing = _Drawing(sheet)
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


def _frame_args(**overrides):
    args = {
        "paper": "A3",
        "orientation": "landscape",
        "title_block": {
            "title": "COLA BOTTLE PETAL BASE",
            "drawing_number": "MCP-0001",
            "material": "PET",
            "scale": "1:1",
            "drawn_by": "MCP",
            "date": "2026-11-05",
            "sheet": "1/1",
            "footer": "UNITS mm",
        },
        "notes": ["1. BREAK SHARP EDGES"],
    }
    args.update(overrides)
    return args


# ── paper ───────────────────────────────────────────────────────────────────


def test_an_ansi_paper_size_is_refused_with_the_reason():
    tools = _tools(_Sheet())
    with pytest.raises(ValueError) as caught:
        tools.execute("catia_drawing_set_sheet", {"paper": "Letter"})
    message = str(caught.value)
    assert "A0, A1, A2, A3, A4" in message
    assert "refuses every one of them" in message, (
        "the refusal has to say WHY Letter is not offered: it is in the enum and "
        "CATIA still raises for it (values 0, 1, 7-12 on B30)")


def test_best_fit_orientation_is_not_offered():
    tools = _tools(_Sheet())
    with pytest.raises(ValueError) as caught:
        tools.execute("catia_drawing_set_sheet", {"orientation": "bestfit"})
    assert "silently" in str(caught.value) or "reads back" in str(caught.value)


def test_set_sheet_reports_the_size_catia_gives_not_the_one_asked_for():
    sheet = _Sheet(paper_size=5, width=420.0, height=297.0)
    tools = _tools(sheet)
    reply = tools.execute("catia_drawing_set_sheet",
                          {"paper": "A3", "orientation": "landscape"})
    assert "A3 420 x 297 mm landscape" in reply
    assert "read back from CATIA" in reply


def test_a_custom_sheet_says_it_has_no_width_instead_of_crashing():
    sheet = _Sheet(paper_size=13, no_width=True)
    tools = _tools(sheet)
    reply = tools.execute("catia_drawing_set_sheet", {})
    assert "will not report a width" in reply
    assert "GetPaperWidth" in reply


# ── the frame ───────────────────────────────────────────────────────────────


def test_a_frame_on_an_unmeasured_sheet_is_refused_by_paper_name():
    sheet = _Sheet(paper_size=13, no_width=True)
    tools = _tools(sheet)
    with pytest.raises(RuntimeError) as caught:
        tools.execute("catia_drawing_add_frame", {})
    message = str(caught.value)
    assert "catPaperUser" in message
    assert "paper='A3'" in message
    assert sheet.background.Factory2D.calls == [], (
        "nothing may be drawn on a sheet whose size CATIA will not state")


def test_the_border_uses_the_iso_filing_margin():
    sheet = _Sheet()
    tools = _tools(sheet)
    tools.execute("catia_drawing_add_frame", _frame_args())
    calls = sheet.background.Factory2D.calls
    assert len(calls) == 4
    xs = {round(value) for line in calls for value in (line[0], line[2])}
    ys = {round(value) for line in calls for value in (line[1], line[3])}
    assert xs == {20, 410}, f"left 20 / right 410 expected, got {sorted(xs)}"
    assert ys == {10, 287}, f"bottom 10 / top 287 expected, got {sorted(ys)}"


def test_the_title_block_anchors_at_its_TOP_left_and_grows_down():
    sheet = _Sheet()
    tools = _tools(sheet)
    tools.execute("catia_drawing_add_frame", _frame_args())
    table = sheet.background.Tables.items[0]
    assert table.x == pytest.approx(230.0), "180 mm wide block flush right"
    assert table.y == pytest.approx(55.0), (
        "Tables.Add takes the TOP edge: 10 margin + 45 high. Passing the bottom "
        "margin (10) drew the block below the sheet on the first run")


def test_the_block_is_merged_into_the_expected_fields():
    sheet = _Sheet()
    tools = _tools(sheet)
    tools.execute("catia_drawing_add_frame", _frame_args())
    table = sheet.background.Tables.items[0]
    assert (1, 1, 1, 5) in table.merges, "title spans five of the eight columns"
    assert (1, 6, 1, 3) in table.merges, "drawing number spans the other three"
    assert (5, 1, 1, 8) in table.merges, "footer is one full-width row"
    assert table.cells[(1, 1)] == "COLA BOTTLE PETAL BASE"
    assert table.cells[(2, 1)] == "MATERIAL"
    assert table.cells[(2, 3)] == "PET"


def test_a_field_left_out_is_empty_rather_than_invented():
    sheet = _Sheet()
    tools = _tools(sheet)
    tools.execute("catia_drawing_add_frame",
                  {"paper": "A3", "title_block": {"title": "ONLY A TITLE"}})
    table = sheet.background.Tables.items[0]
    assert table.cells[(2, 3)] == "", "material was not given, so it stays empty"
    assert table.cells[(1, 6)] == ""


def test_notes_are_placed_above_the_block():
    sheet = _Sheet()
    tools = _tools(sheet)
    tools.execute("catia_drawing_add_frame", _frame_args())
    note = sheet.background.Texts.items[0]
    assert note.Text == "1. BREAK SHARP EDGES"
    assert note.y > 55.0, "the block's top is y=55; notes go above it"
    assert note.AnchorPosition == 3, "catBottomLeft, so the line grows upward"


def test_an_oversized_block_is_refused_before_anything_is_drawn():
    sheet = _Sheet()
    tools = _tools(sheet)
    before = sheet.background.GeometricElements.Count
    with pytest.raises(ValueError) as caught:
        tools.execute("catia_drawing_add_frame",
                      {"paper": "A3", "title_block": {"width": 500}})
    assert "leaves" in str(caught.value)
    assert sheet.background.Factory2D.calls == [], (
        "the first version drew the border and THEN refused the block, leaving "
        "four stray lines (Background elements 5 -> 9 on B30)")
    assert sheet.background.GeometricElements.Count == before


def test_a_refused_border_says_so_instead_of_returning_nothing_drawn():
    sheet = _Sheet(fail_lines=True)
    tools = _tools(sheet)
    with pytest.raises(RuntimeError) as caught:
        tools.execute("catia_drawing_add_frame", _frame_args())
    assert "border could not be drawn" in str(caught.value)


def test_a_failed_merge_is_reported_not_swallowed():
    sheet = _Sheet(fail_merge=True)
    tools = _tools(sheet)
    reply = tools.execute("catia_drawing_add_frame", _frame_args())
    assert "NOT EVERYTHING TOOK" in reply
    assert "merge" in reply


def test_the_reply_states_the_drawing_area():
    sheet = _Sheet()
    tools = _tools(sheet)
    reply = tools.execute("catia_drawing_add_frame", _frame_args())
    assert "x 20..410" in reply and "y 10..287" in reply, (
        "views are placed by coordinates, and one landed on the border in the "
        "A3 acceptance run - the area is the number the caller needs")


def test_a_sheet_without_a_background_view_is_refused():
    sheet = _Sheet(views=1)
    tools = _tools(sheet)
    with pytest.raises(RuntimeError) as caught:
        tools.execute("catia_drawing_add_frame", _frame_args())
    assert "Background View" in str(caught.value)


def test_the_two_new_tools_are_declared():
    tools = DraftingTools(_Conn(_Sheet()))
    names = {d["name"] for d in tools.get_tool_definitions()}
    assert {"catia_drawing_set_sheet", "catia_drawing_add_frame"} <= names
    assert len(names) == 13
