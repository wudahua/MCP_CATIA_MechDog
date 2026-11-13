"""Read a real font's glyph OUTLINES, with no third-party library.

WHY NO fontTools. This package ships three dependencies on purpose: a customer's
B30 has to be able to ``pip install`` it and get a working server. A CJK glyph
needs a real font, not a library - Windows, macOS and most Linux desktops already
have one - so what was missing was a reader, and a reader is 200 lines of
``struct`` work.

WHAT IT RETURNS: CLOSED CONTOURS in font units, y up, origin on the baseline -
the OUTLINE of the character, not its middle. That is deliberately a different
thing from the Hershey face ``catia_mcp.fonts`` uses for ASCII: an outline gives
every stem two walls, and a groove a fraction of a millimetre deep cannot resolve
two walls a fraction of a millimetre apart (see that module's docstring). The
outline is what you want when you are going to PAD the glyph, or engrave its
contour; it is the wrong thing for a single-line groove.

WHY IT IS STILL WORTH HAVING: there is no single-line CJK face in the public
domain that covers more than a few hundred characters. Hand-authoring strokes for
a Chinese word is exactly the work the 2026-11-11 report complained about. An
outline of 汉字 from the machine's own font is real geometry, drawn in one call,
and it pads and pockets correctly - which is most of what the caller wanted.

Verified against the fonts on B30 2026-11-14: simhei.ttf, msyh.ttc (face 0),
Deng.ttf; cmap formats 4 and 12, simple and composite glyphs.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Iterator

#: Composite glyph flags (OpenType spec, 'glyf').
_ARG_1_AND_2_ARE_WORDS = 0x0001
_ARGS_ARE_XY_VALUES = 0x0002
_WE_HAVE_A_SCALE = 0x0008
_MORE_COMPONENTS = 0x0020
_WE_HAVE_AN_X_AND_Y_SCALE = 0x0040
_WE_HAVE_A_TWO_BY_TWO = 0x0080

#: How deep a composite glyph may nest before this gives up. Real fonts nest one
#: or two deep; a cycle in a corrupt file would otherwise recurse forever.
_MAX_COMPOSITE_DEPTH = 6


class FontFormatError(Exception):
    """The file is not a font this reader understands."""


def _u8(data: bytes, offset: int) -> int:
    return data[offset]


def _i8(data: bytes, offset: int) -> int:
    return struct.unpack_from(">b", data, offset)[0]


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


def _i16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">h", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _f2dot14(data: bytes, offset: int) -> float:
    return _i16(data, offset) / 16384.0


@dataclass
class _Contour:
    """One closed contour in font units, as points with an on-curve flag."""

    points: list[tuple[float, float, bool]] = field(default_factory=list)


@dataclass
class Glyph:
    #: Closed polylines, font units, y up. Each one repeats its first point.
    contours: list[list[tuple[float, float]]]
    #: Horizontal advance in font units.
    advance: float


class TrueTypeFont:
    """A parsed TrueType/OpenType file, one face at a time.

    ``face`` picks a face out of a ``.ttc`` collection; single-face files ignore
    it. Everything is lazy except the table directory, so asking one character
    does not decode the other twenty thousand glyphs.
    """

    def __init__(self, path: str, face: int = 0) -> None:
        with open(path, "rb") as handle:
            self._data = handle.read()
        self.path = path
        self._base = self._face_offset(face)
        self._tables = self._read_directory(self._base)
        for required in ("head", "maxp", "cmap", "loca", "glyf"):
            if required not in self._tables:
                raise FontFormatError(
                    f"{path}: no '{required}' table, so this is not a TrueType "
                    "outline font (a CFF/OpenType .otf has 'CFF ' instead and is "
                    "not readable here)")
        head = self._tables["head"][0]
        self.units_per_em = _u16(self._data, head + 18)
        self._index_to_loc = _i16(self._data, head + 50)
        self.num_glyphs = _u16(self._data, self._tables["maxp"][0] + 4)
        self._loca = self._read_loca()
        self._cmap = self._choose_cmap()
        self._advances = self._read_advances()
        self.family = self._read_family()
        self.cap_height = self._read_cap_height()

    # ── file plumbing ────────────────────────────────────────────────────

    def _face_offset(self, face: int) -> int:
        data = self._data
        if data[:4] == b"ttcf":
            count = _u32(data, 8)
            if not 0 <= face < count:
                raise FontFormatError(
                    f"{self.path}: face {face} asked for, the collection holds "
                    f"{count}")
            return _u32(data, 12 + 4 * face)
        if face:
            raise FontFormatError(
                f"{self.path} is a single-face font, so face {face} does not "
                "exist - drop the face argument")
        return 0

    def _read_directory(self, base: int) -> dict[str, tuple[int, int]]:
        tag = self._data[base:base + 4]
        if tag not in (b"\x00\x01\x00\x00", b"true", b"OTTO", b"ttcf"):
            raise FontFormatError(
                f"{self.path}: not a TrueType file (signature {tag!r})")
        if tag == b"OTTO":
            raise FontFormatError(
                f"{self.path} is CFF-flavoured OpenType ('OTTO'): its outlines "
                "are PostScript, not quadratic, and this reader does not decode "
                "'CFF '. Use a TrueType font instead (simhei.ttf, msyh.ttc, "
                "Deng.ttf on Windows; a .ttf Noto CJK elsewhere).")
        count = _u16(self._data, base + 4)
        tables: dict[str, tuple[int, int]] = {}
        for index in range(count):
            record = base + 12 + 16 * index
            name = self._data[record:record + 4].decode("latin-1")
            tables[name] = (_u32(self._data, record + 8),
                            _u32(self._data, record + 12))
        return tables

    def _read_family(self) -> str:
        try:
            offset = self._tables["name"][0]
        except KeyError:
            return ""
        data = self._data
        count = _u16(data, offset + 2)
        storage = offset + _u16(data, offset + 4)
        for index in range(count):
            record = offset + 6 + 12 * index
            platform = _u16(data, record)
            name_id = _u16(data, record + 6)
            if name_id != 1:
                continue
            length = _u16(data, record + 8)
            start = storage + _u16(data, record + 10)
            raw = data[start:start + length]
            try:
                return (raw.decode("utf-16-be") if platform == 3
                        else raw.decode("latin-1")).strip("\x00").strip()
            except UnicodeDecodeError:
                continue
        return ""

    def _read_cap_height(self) -> float:
        """Cap height in font units, or 0.7 em when the table does not say.

        OS/2 version 2 and later carry sCapHeight; version 0 and 1 do not, and
        0.7 em is what a Latin face measures within a few percent. For a CJK
        string this number only sets the SCALE (see fonts.text_to_outlines): an
        ideograph is taller than a Latin capital, so it comes out larger than the
        asked-for height, and the reply prints the box it actually occupies.
        """
        entry = self._tables.get("OS/2")
        if entry:
            offset, length = entry
            version = _u16(self._data, offset)
            if version >= 2 and length >= 90:
                cap = _u16(self._data, offset + 88)
                if cap:
                    return float(cap)
        return 0.7 * self.units_per_em

    # ── cmap ─────────────────────────────────────────────────────────────

    def _choose_cmap(self) -> tuple[int, int]:
        """(subtable offset, format) of the best Unicode mapping in the file."""
        data = self._data
        offset = self._tables["cmap"][0]
        count = _u16(data, offset + 2)
        best: tuple[int, int, int] | None = None   # (rank, offset, format)
        for index in range(count):
            record = offset + 4 + 8 * index
            platform = _u16(data, record)
            encoding = _u16(data, record + 2)
            sub = offset + _u32(data, record + 4)
            fmt = _u16(data, sub)
            if fmt not in (4, 12):
                continue
            rank = {(3, 10): 5, (0, 6): 5, (0, 4): 4, (3, 1): 3,
                    (0, 3): 2, (0, 2): 2, (0, 1): 2, (0, 0): 1,
                    (3, 0): 1}.get((platform, encoding), 0)
            if rank and (best is None or rank > best[0]):
                best = (rank, sub, fmt)
        if best is None:
            raise FontFormatError(
                f"{self.path}: no Unicode cmap subtable (formats 4 or 12) - the "
                "file maps characters some other way and this reader cannot "
                "resolve them")
        return best[1], best[2]

    def _glyph_index(self, code: int) -> int:
        data = self._data
        sub, fmt = self._cmap
        if fmt == 12:
            groups = _u32(data, sub + 12)
            for index in range(groups):
                record = sub + 16 + 12 * index
                start = _u32(data, record)
                end = _u32(data, record + 4)
                if start <= code <= end:
                    return _u32(data, record + 8) + (code - start)
            return 0
        if code > 0xFFFF:
            return 0
        segments = _u16(data, sub + 6) // 2
        ends_at = sub + 14
        starts_at = ends_at + 2 * segments + 2
        deltas_at = starts_at + 2 * segments
        ranges_at = deltas_at + 2 * segments
        # The spec orders the segments; a linear walk is fine for one lookup and
        # avoids a bisect that has to be kept honest.
        for index in range(segments):
            if _u16(data, ends_at + 2 * index) < code:
                continue
            start = _u16(data, starts_at + 2 * index)
            if start > code:
                return 0
            delta = _i16(data, deltas_at + 2 * index)
            range_offset = _u16(data, ranges_at + 2 * index)
            if range_offset == 0:
                return (code + delta) & 0xFFFF
            position = ranges_at + 2 * index + range_offset + 2 * (code - start)
            if position + 2 > len(data):
                return 0
            glyph = _u16(data, position)
            return 0 if glyph == 0 else (glyph + delta) & 0xFFFF
        return 0

    # ── metrics and outlines ─────────────────────────────────────────────

    def _read_loca(self) -> list[int]:
        offset = self._tables["loca"][0]
        count = self.num_glyphs + 1
        if self._index_to_loc == 0:
            return [_u16(self._data, offset + 2 * i) * 2 for i in range(count)]
        return [_u32(self._data, offset + 4 * i) for i in range(count)]

    def _read_advances(self) -> list[int]:
        if "hmtx" not in self._tables or "hhea" not in self._tables:
            return []
        metrics = _u16(self._data, self._tables["hhea"][0] + 34)
        offset = self._tables["hmtx"][0]
        advances = []
        for index in range(metrics):
            advances.append(_u16(self._data, offset + 4 * index))
        # Glyphs past numberOfHMetrics repeat the last advance; the trailing
        # left-side-bearings do not matter here.
        while len(advances) < self.num_glyphs and advances:
            advances.append(advances[-1])
        return advances

    def advance(self, glyph: int) -> float:
        if 0 <= glyph < len(self._advances):
            return float(self._advances[glyph])
        return float(self.units_per_em) / 2.0

    def has_char(self, char: str) -> bool:
        return bool(char) and self._glyph_index(ord(char)) != 0

    def glyph(self, code: int, flatten: float = 0.0) -> Glyph | None:
        """The glyph for one CODE POINT, or None when the font has none.

        ``flatten`` is the maximum chord error in font units: quadratic curves
        are subdivided until a straight chord is that close. 0 means "no
        subdivision beyond the on-curve points", which is only useful for tests.
        """
        glyph = self._glyph_index(code)
        if glyph == 0:
            return None
        contours = self._outlines(glyph, 0)
        return Glyph(
            contours=[[(x, y) for x, y in _closed(contour, flatten, self.units_per_em)]
                      for contour in contours],
            advance=self.advance(glyph),
        )

    def _outlines(self, glyph: int, depth: int) -> list[list[tuple[float, float, bool]]]:
        """Raw contours for a glyph id: control points with on-curve flags."""
        if not 0 <= glyph < self.num_glyphs:
            return []
        start = self._loca[glyph]
        end = self._loca[glyph + 1]
        if end <= start:
            return []                      # empty glyph, e.g. the space
        data = self._data
        offset = self._tables["glyf"][0] + start
        contours = _i16(data, offset)
        if contours >= 0:
            return self._simple_glyph(offset, contours)
        if depth >= _MAX_COMPOSITE_DEPTH:
            return []
        return self._composite_glyph(offset, depth)

    def _simple_glyph(self, offset: int, contours: int) -> list[list[tuple[float, float, bool]]]:
        data = self._data
        ends = [_u16(data, offset + 10 + 2 * i) for i in range(contours)]
        if not ends:
            return []
        total = ends[-1] + 1
        instructions = _u16(data, offset + 10 + 2 * contours)
        position = offset + 12 + 2 * contours + instructions

        flags: list[int] = []
        while len(flags) < total:
            flag = _u8(data, position)
            position += 1
            flags.append(flag)
            if flag & 0x08:                # REPEAT
                repeat = _u8(data, position)
                position += 1
                flags.extend([flag] * repeat)

        xs: list[int] = []
        current = 0
        for flag in flags:
            if flag & 0x02:                # X_SHORT_VECTOR
                delta = _u8(data, position)
                position += 1
                current += delta if flag & 0x10 else -delta
            elif not flag & 0x10:          # not SAME_X
                current += _i16(data, position)
                position += 2
            xs.append(current)

        ys: list[int] = []
        current = 0
        for flag in flags:
            if flag & 0x04:                # Y_SHORT_VECTOR
                delta = _u8(data, position)
                position += 1
                current += delta if flag & 0x20 else -delta
            elif not flag & 0x20:
                current += _i16(data, position)
                position += 2
            ys.append(current)

        points = [(float(xs[i]), float(ys[i]), bool(flags[i] & 0x01))
                  for i in range(total)]
        result = []
        first = 0
        for end in ends:
            chunk = points[first:end + 1]
            if chunk:
                result.append(chunk)
            first = end + 1
        return result

    def _composite_glyph(self, offset: int,
                         depth: int) -> list[list[tuple[float, float, bool]]]:
        data = self._data
        result: list[list[tuple[float, float, bool]]] = []
        position = offset + 10
        while True:
            flags = _u16(data, position)
            component = _u16(data, position + 2)
            position += 4
            if flags & _ARG_1_AND_2_ARE_WORDS:
                if flags & _ARGS_ARE_XY_VALUES:
                    dx, dy = _i16(data, position), _i16(data, position + 2)
                else:
                    dx = dy = 0            # point matching: not used by real fonts
                position += 4
            else:
                if flags & _ARGS_ARE_XY_VALUES:
                    dx, dy = _i8(data, position), _i8(data, position + 1)
                else:
                    dx = dy = 0
                position += 2
            a = d = 1.0
            b = c = 0.0
            if flags & _WE_HAVE_A_SCALE:
                a = d = _f2dot14(data, position)
                position += 2
            elif flags & _WE_HAVE_AN_X_AND_Y_SCALE:
                a, d = _f2dot14(data, position), _f2dot14(data, position + 2)
                position += 4
            elif flags & _WE_HAVE_A_TWO_BY_TWO:
                a, b = _f2dot14(data, position), _f2dot14(data, position + 2)
                c, d = _f2dot14(data, position + 4), _f2dot14(data, position + 6)
                position += 8
            for contour in self._outlines(component, depth + 1):
                result.append([(a * x + c * y + dx, b * x + d * y + dy, on)
                               for x, y, on in contour])
            if not flags & _MORE_COMPONENTS:
                break
        return result

    def chars(self) -> Iterator[str]:
        """The BMP characters this font maps, for a readable error message."""
        for code in range(0x20, 0x3000):
            if self._glyph_index(code):
                yield chr(code)


def _closed(contour: list[tuple[float, float, bool]], flatten: float,
            units_per_em: int) -> list[tuple[float, float]]:
    """One contour as a closed polyline, quadratic curves subdivided.

    The on/off-curve walk follows the spec: consecutive off-curve points imply an
    on-curve point at their midpoint, and a contour that starts off-curve starts
    at the midpoint of its last and first point.
    """
    if not contour:
        return []
    points = list(contour)
    # Rotate so the walk starts on an on-curve point.
    start = next((index for index, point in enumerate(points) if point[2]), None)
    if start is None:
        # All off-curve (a circle drawn as four controls): start at the implied
        # midpoint between the last and the first.
        first = ((points[0][0] + points[-1][0]) / 2.0,
                 (points[0][1] + points[-1][1]) / 2.0)
        ordered = [(first[0], first[1], True)] + points
    else:
        rotated = points[start:] + points[:start]
        ordered = [rotated[0]] + rotated[1:] + [rotated[0]]

    out: list[tuple[float, float]] = [(ordered[0][0], ordered[0][1])]
    pending: tuple[float, float] | None = None
    for x, y, on in ordered[1:]:
        if on:
            if pending is None:
                out.append((x, y))
            else:
                out.extend(_quadratic(out[-1], pending, (x, y), flatten,
                                      units_per_em))
                pending = None
        else:
            if pending is not None:
                mid = ((pending[0] + x) / 2.0, (pending[1] + y) / 2.0)
                out.extend(_quadratic(out[-1], pending, mid, flatten,
                                      units_per_em))
            pending = (x, y)
    if pending is not None:
        out.extend(_quadratic(out[-1], pending, out[0], flatten, units_per_em))
    if out[-1] != out[0]:
        out.append(out[0])
    return out


def _quadratic(start: tuple[float, float], control: tuple[float, float],
               end: tuple[float, float], flatten: float,
               units_per_em: int) -> list[tuple[float, float]]:
    """Points along one quadratic segment, end EXCLUSIVE, start exclusive."""
    if flatten <= 0:
        return [end]
    span = (abs(start[0] - control[0]) + abs(start[1] - control[1])
            + abs(end[0] - control[0]) + abs(end[1] - control[1]))
    step = max(flatten, units_per_em * 1e-4)
    steps = max(1, min(24, int(span / step + 0.5)))
    if steps <= 1:
        return [end]
    points = []
    for index in range(1, steps):
        t = index / steps
        u = 1.0 - t
        points.append((u * u * start[0] + 2 * u * t * control[0] + t * t * end[0],
                       u * u * start[1] + 2 * u * t * control[1] + t * t * end[1]))
    points.append(end)
    return points
