"""Hershey Simplex, as single-stroke polylines.

WHY THIS IS HERE. ``Factory2D`` has no ``CreateText`` - 37 members on B30 and
none of them makes text (measured 2026-11-10). A glyph therefore has to arrive
as GEOMETRY, and ``catia_sketch_strokes`` already takes a whole outline in one
call. What was missing was the font itself: the 2026-11-11 report put it as
"the title's text still needs its point sets handed over by hand". This is that
font, so the hand work goes away.

WHY HERSHEY SIMPLEX. It is a SINGLE-LINE face: one stroke per stem. An outline
font (TrueType and friends) would give every stem two walls a fraction of a
millimetre apart, and a groove 0.4 mm deep cannot resolve two walls that close -
it comes out as one ragged slot. Simplex is also the ancestor of the SHX faces
CATIA's own drafting uses, so the result looks like engineering lettering rather
than a shrunken poster.

ACKNOWLEDGEMENT - required by the licence and therefore kept in full:
  The Hershey Fonts were originally created by Dr. A. V. Hershey while working
  at the U. S. National Bureau of Standards. The format of the font data in this
  distribution was originally created by James Hurt, Cognition, Inc.
The Hershey data may be used for any purpose, commercial or otherwise, provided
this acknowledgement is distributed with it. The "futural" face below is the
simplex set; the strings are the file's own encoding, unpacked by
``parse_glyph``.

THE FILE'S OWN COORDINATES, because they are not the caller's: y grows
DOWNWARD, the baseline sits at y = 9 and a capital letter spans y = -12 .. 9,
so a capital is 21 units tall. :func:`text_to_strokes` flips the y axis and
scales to millimetres, and every number it returns has y UP with the baseline
at the origin.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from catia_mcp.results import ArgumentRejected

#: How tall a capital letter is, in the font's own units.
CAP_HEIGHT_UNITS = 21.0
#: Where the baseline sits in the font's own units (y grows downward).
BASELINE_UNITS = 9.0
#: Gap put between two glyphs, as a fraction of the asked-for cap height. The
#: bearings alone are 1-2 units, which reads as letters touching.
DEFAULT_SPACING_RATIO = 0.2
#: Baseline-to-baseline distance for a second line, as a fraction of the cap
#: height. 1.6 is what a drawing sheet uses.
DEFAULT_LINE_RATIO = 1.6

_GLYPHS: dict[str, str] = {
    ' ': '-8 8',
    '!': '-5 5 M 0 -12 L 0 2 M 0 7 L -1 8 L 0 9 L 1 8 L 0 7',
    '"': '-8 8 M -4 -12 L -4 -5 M 4 -12 L 4 -5',
    '#': '-10 11 M 1 -16 L -6 16 M 7 -16 L 0 16 M -6 -3 L 8 -3 M -7 3 L 7 3',
    '$': '-10 10 M -2 -16 L -2 13 M 2 -16 L 2 13 M 7 -9 L 5 -11 L 2 -12 L -2 -12 L -5 -11 L -7 -9 L -7 -7 L -6 -5 L -5 -4 L -3 -3 L 3 -1 L 5 0 L 6 1 L 7 3 L 7 6 L 5 8 L 2 9 L -2 9 L -5 8 L -7 6',
    '%': '-12 12 M 9 -12 L -9 9 M -4 -12 L -2 -10 L -2 -8 L -3 -6 L -5 -5 L -7 -5 L -9 -7 L -9 -9 L -8 -11 L -6 -12 L -4 -12 L -2 -11 L 1 -10 L 4 -10 L 7 -11 L 9 -12 M 5 2 L 3 3 L 2 5 L 2 7 L 4 9 L 6 9 L 8 8 L 9 6 L 9 4 L 7 2 L 5 2',
    '&': '-13 13 M 10 -3 L 10 -4 L 9 -5 L 8 -5 L 7 -4 L 6 -2 L 4 3 L 2 6 L 0 8 L -2 9 L -6 9 L -8 8 L -9 7 L -10 5 L -10 3 L -9 1 L -8 0 L -1 -4 L 0 -5 L 1 -7 L 1 -9 L 0 -11 L -2 -12 L -4 -11 L -5 -9 L -5 -7 L -4 -4 L -2 -1 L 3 6 L 5 8 L 7 9 L 9 9 L 10 8 L 10 7',
    "'": '-5 5 M 0 -10 L -1 -11 L 0 -12 L 1 -11 L 1 -9 L 0 -7 L -1 -6',
    '(': '-7 7 M 4 -16 L 2 -14 L 0 -11 L -2 -7 L -3 -2 L -3 2 L -2 7 L 0 11 L 2 14 L 4 16',
    ')': '-7 7 M -4 -16 L -2 -14 L 0 -11 L 2 -7 L 3 -2 L 3 2 L 2 7 L 0 11 L -2 14 L -4 16',
    '*': '-8 8 M 0 -6 L 0 6 M -5 -3 L 5 3 M 5 -3 L -5 3',
    '+': '-13 13 M 0 -9 L 0 9 M -9 0 L 9 0',
    ',': '-4 4 M 1 5 L 0 6 L -1 5 L 0 4 L 1 5 L 1 7 L -1 9',
    '-': '-13 13 M -9 0 L 9 0',
    '.': '-4 4 M 0 4 L -1 5 L 0 6 L 1 5 L 0 4',
    '/': '-11 11 M 9 -16 L -9 16',
    '0': '-10 10 M -1 -12 L -4 -11 L -6 -8 L -7 -3 L -7 0 L -6 5 L -4 8 L -1 9 L 1 9 L 4 8 L 6 5 L 7 0 L 7 -3 L 6 -8 L 4 -11 L 1 -12 L -1 -12',
    '1': '-10 10 M -4 -8 L -2 -9 L 1 -12 L 1 9',
    '2': '-10 10 M -6 -7 L -6 -8 L -5 -10 L -4 -11 L -2 -12 L 2 -12 L 4 -11 L 5 -10 L 6 -8 L 6 -6 L 5 -4 L 3 -1 L -7 9 L 7 9',
    '3': '-10 10 M -5 -12 L 6 -12 L 0 -4 L 3 -4 L 5 -3 L 6 -2 L 7 1 L 7 3 L 6 6 L 4 8 L 1 9 L -2 9 L -5 8 L -6 7 L -7 5',
    '4': '-10 10 M 3 -12 L -7 2 L 8 2 M 3 -12 L 3 9',
    '5': '-10 10 M 5 -12 L -5 -12 L -6 -3 L -5 -4 L -2 -5 L 1 -5 L 4 -4 L 6 -2 L 7 1 L 7 3 L 6 6 L 4 8 L 1 9 L -2 9 L -5 8 L -6 7 L -7 5',
    '6': '-10 10 M 6 -9 L 5 -11 L 2 -12 L 0 -12 L -3 -11 L -5 -8 L -6 -3 L -6 2 L -5 6 L -3 8 L 0 9 L 1 9 L 4 8 L 6 6 L 7 3 L 7 2 L 6 -1 L 4 -3 L 1 -4 L 0 -4 L -3 -3 L -5 -1 L -6 2',
    '7': '-10 10 M 7 -12 L -3 9 M -7 -12 L 7 -12',
    '8': '-10 10 M -2 -12 L -5 -11 L -6 -9 L -6 -7 L -5 -5 L -3 -4 L 1 -3 L 4 -2 L 6 0 L 7 2 L 7 5 L 6 7 L 5 8 L 2 9 L -2 9 L -5 8 L -6 7 L -7 5 L -7 2 L -6 0 L -4 -2 L -1 -3 L 3 -4 L 5 -5 L 6 -7 L 6 -9 L 5 -11 L 2 -12 L -2 -12',
    '9': '-10 10 M 6 -5 L 5 -2 L 3 0 L 0 1 L -1 1 L -4 0 L -6 -2 L -7 -5 L -7 -6 L -6 -9 L -4 -11 L -1 -12 L 0 -12 L 3 -11 L 5 -9 L 6 -5 L 6 0 L 5 5 L 3 8 L 0 9 L -2 9 L -5 8 L -6 6',
    ':': '-4 4 M 0 -3 L -1 -2 L 0 -1 L 1 -2 L 0 -3 M 0 4 L -1 5 L 0 6 L 1 5 L 0 4',
    ';': '-4 4 M 0 -3 L -1 -2 L 0 -1 L 1 -2 L 0 -3 M 1 5 L 0 6 L -1 5 L 0 4 L 1 5 L 1 7 L -1 9',
    '<': '-12 12 M 8 -9 L -8 0 L 8 9',
    '=': '-13 13 M -9 -3 L 9 -3 M -9 3 L 9 3',
    '>': '-12 12 M -8 -9 L 8 0 L -8 9',
    '?': '-9 9 M -6 -7 L -6 -8 L -5 -10 L -4 -11 L -2 -12 L 2 -12 L 4 -11 L 5 -10 L 6 -8 L 6 -6 L 5 -4 L 4 -3 L 0 -1 L 0 2 M 0 7 L -1 8 L 0 9 L 1 8 L 0 7',
    '@': '-13 14 M 5 -4 L 4 -6 L 2 -7 L -1 -7 L -3 -6 L -4 -5 L -5 -2 L -5 1 L -4 3 L -2 4 L 1 4 L 3 3 L 4 1 M -1 -7 L -3 -5 L -4 -2 L -4 1 L -3 3 L -2 4 M 5 -7 L 4 1 L 4 3 L 6 4 L 8 4 L 10 2 L 11 -1 L 11 -3 L 10 -6 L 9 -8 L 7 -10 L 5 -11 L 2 -12 L -1 -12 L -4 -11 L -6 -10 L -8 -8 L -9 -6 L -10 -3 L -10 0 L -9 3 L -8 5 L -6 7 L -4 8 L -1 9 L 2 9 L 5 8 L 7 7 L 8 6 M 6 -7 L 5 1 L 5 3 L 6 4',
    'A': '-9 9 M 0 -12 L -8 9 M 0 -12 L 8 9 M -5 2 L 5 2',
    'B': '-11 10 M -7 -12 L -7 9 M -7 -12 L 2 -12 L 5 -11 L 6 -10 L 7 -8 L 7 -6 L 6 -4 L 5 -3 L 2 -2 M -7 -2 L 2 -2 L 5 -1 L 6 0 L 7 2 L 7 5 L 6 7 L 5 8 L 2 9 L -7 9',
    'C': '-10 11 M 8 -7 L 7 -9 L 5 -11 L 3 -12 L -1 -12 L -3 -11 L -5 -9 L -6 -7 L -7 -4 L -7 1 L -6 4 L -5 6 L -3 8 L -1 9 L 3 9 L 5 8 L 7 6 L 8 4',
    'D': '-11 10 M -7 -12 L -7 9 M -7 -12 L 0 -12 L 3 -11 L 5 -9 L 6 -7 L 7 -4 L 7 1 L 6 4 L 5 6 L 3 8 L 0 9 L -7 9',
    'E': '-10 9 M -6 -12 L -6 9 M -6 -12 L 7 -12 M -6 -2 L 2 -2 M -6 9 L 7 9',
    'F': '-10 8 M -6 -12 L -6 9 M -6 -12 L 7 -12 M -6 -2 L 2 -2',
    'G': '-10 11 M 8 -7 L 7 -9 L 5 -11 L 3 -12 L -1 -12 L -3 -11 L -5 -9 L -6 -7 L -7 -4 L -7 1 L -6 4 L -5 6 L -3 8 L -1 9 L 3 9 L 5 8 L 7 6 L 8 4 L 8 1 M 3 1 L 8 1',
    'H': '-11 11 M -7 -12 L -7 9 M 7 -12 L 7 9 M -7 -2 L 7 -2',
    'I': '-4 4 M 0 -12 L 0 9',
    'J': '-8 8 M 4 -12 L 4 4 L 3 7 L 2 8 L 0 9 L -2 9 L -4 8 L -5 7 L -6 4 L -6 2',
    'K': '-11 10 M -7 -12 L -7 9 M 7 -12 L -7 2 M -2 -3 L 7 9',
    'L': '-10 7 M -6 -12 L -6 9 M -6 9 L 6 9',
    'M': '-12 12 M -8 -12 L -8 9 M -8 -12 L 0 9 M 8 -12 L 0 9 M 8 -12 L 8 9',
    'N': '-11 11 M -7 -12 L -7 9 M -7 -12 L 7 9 M 7 -12 L 7 9',
    'O': '-11 11 M -2 -12 L -4 -11 L -6 -9 L -7 -7 L -8 -4 L -8 1 L -7 4 L -6 6 L -4 8 L -2 9 L 2 9 L 4 8 L 6 6 L 7 4 L 8 1 L 8 -4 L 7 -7 L 6 -9 L 4 -11 L 2 -12 L -2 -12',
    'P': '-11 10 M -7 -12 L -7 9 M -7 -12 L 2 -12 L 5 -11 L 6 -10 L 7 -8 L 7 -5 L 6 -3 L 5 -2 L 2 -1 L -7 -1',
    'Q': '-11 11 M -2 -12 L -4 -11 L -6 -9 L -7 -7 L -8 -4 L -8 1 L -7 4 L -6 6 L -4 8 L -2 9 L 2 9 L 4 8 L 6 6 L 7 4 L 8 1 L 8 -4 L 7 -7 L 6 -9 L 4 -11 L 2 -12 L -2 -12 M 1 5 L 7 11',
    'R': '-11 10 M -7 -12 L -7 9 M -7 -12 L 2 -12 L 5 -11 L 6 -10 L 7 -8 L 7 -6 L 6 -4 L 5 -3 L 2 -2 L -7 -2 M 0 -2 L 7 9',
    'S': '-10 10 M 7 -9 L 5 -11 L 2 -12 L -2 -12 L -5 -11 L -7 -9 L -7 -7 L -6 -5 L -5 -4 L -3 -3 L 3 -1 L 5 0 L 6 1 L 7 3 L 7 6 L 5 8 L 2 9 L -2 9 L -5 8 L -7 6',
    'T': '-8 8 M 0 -12 L 0 9 M -7 -12 L 7 -12',
    'U': '-11 11 M -7 -12 L -7 3 L -6 6 L -4 8 L -1 9 L 1 9 L 4 8 L 6 6 L 7 3 L 7 -12',
    'V': '-9 9 M -8 -12 L 0 9 M 8 -12 L 0 9',
    'W': '-12 12 M -10 -12 L -5 9 M 0 -12 L -5 9 M 0 -12 L 5 9 M 10 -12 L 5 9',
    'X': '-10 10 M -7 -12 L 7 9 M 7 -12 L -7 9',
    'Y': '-9 9 M -8 -12 L 0 -2 L 0 9 M 8 -12 L 0 -2',
    'Z': '-10 10 M 7 -12 L -7 9 M -7 -12 L 7 -12 M -7 9 L 7 9',
    '[': '-7 7 M -3 -16 L -3 16 M -2 -16 L -2 16 M -3 -16 L 4 -16 M -3 16 L 4 16',
    '\\': '-7 7 M -7 -12 L 7 12',
    ']': '-7 7 M 2 -16 L 2 16 M 3 -16 L 3 16 M -4 -16 L 3 -16 M -4 16 L 3 16',
    '^': '-8 8 M 0 -14 L -8 0 M 0 -14 L 8 0',
    '_': '-9 9 M -9 16 L 9 16',
    '`': '-4 4 M 1 -7 L -1 -5 L -1 -3 L 0 -2 L 1 -3 L 0 -4 L -1 -3',
    'a': '-9 10 M 6 -5 L 6 9 M 6 -2 L 4 -4 L 2 -5 L -1 -5 L -3 -4 L -5 -2 L -6 1 L -6 3 L -5 6 L -3 8 L -1 9 L 2 9 L 4 8 L 6 6',
    'b': '-10 9 M -6 -12 L -6 9 M -6 -2 L -4 -4 L -2 -5 L 1 -5 L 3 -4 L 5 -2 L 6 1 L 6 3 L 5 6 L 3 8 L 1 9 L -2 9 L -4 8 L -6 6',
    'c': '-9 9 M 6 -2 L 4 -4 L 2 -5 L -1 -5 L -3 -4 L -5 -2 L -6 1 L -6 3 L -5 6 L -3 8 L -1 9 L 2 9 L 4 8 L 6 6',
    'd': '-9 10 M 6 -12 L 6 9 M 6 -2 L 4 -4 L 2 -5 L -1 -5 L -3 -4 L -5 -2 L -6 1 L -6 3 L -5 6 L -3 8 L -1 9 L 2 9 L 4 8 L 6 6',
    'e': '-9 9 M -6 1 L 6 1 L 6 -1 L 5 -3 L 4 -4 L 2 -5 L -1 -5 L -3 -4 L -5 -2 L -6 1 L -6 3 L -5 6 L -3 8 L -1 9 L 2 9 L 4 8 L 6 6',
    'f': '-5 7 M 5 -12 L 3 -12 L 1 -11 L 0 -8 L 0 9 M -3 -5 L 4 -5',
    'g': '-9 10 M 6 -5 L 6 11 L 5 14 L 4 15 L 2 16 L -1 16 L -3 15 M 6 -2 L 4 -4 L 2 -5 L -1 -5 L -3 -4 L -5 -2 L -6 1 L -6 3 L -5 6 L -3 8 L -1 9 L 2 9 L 4 8 L 6 6',
    'h': '-9 10 M -5 -12 L -5 9 M -5 -1 L -2 -4 L 0 -5 L 3 -5 L 5 -4 L 6 -1 L 6 9',
    'i': '-4 4 M -1 -12 L 0 -11 L 1 -12 L 0 -13 L -1 -12 M 0 -5 L 0 9',
    'j': '-5 5 M 0 -12 L 1 -11 L 2 -12 L 1 -13 L 0 -12 M 1 -5 L 1 12 L 0 15 L -2 16 L -4 16',
    'k': '-9 8 M -5 -12 L -5 9 M 5 -5 L -5 5 M -1 1 L 6 9',
    'l': '-4 4 M 0 -12 L 0 9',
    'm': '-15 15 M -11 -5 L -11 9 M -11 -1 L -8 -4 L -6 -5 L -3 -5 L -1 -4 L 0 -1 L 0 9 M 0 -1 L 3 -4 L 5 -5 L 8 -5 L 10 -4 L 11 -1 L 11 9',
    'n': '-9 10 M -5 -5 L -5 9 M -5 -1 L -2 -4 L 0 -5 L 3 -5 L 5 -4 L 6 -1 L 6 9',
    'o': '-9 10 M -1 -5 L -3 -4 L -5 -2 L -6 1 L -6 3 L -5 6 L -3 8 L -1 9 L 2 9 L 4 8 L 6 6 L 7 3 L 7 1 L 6 -2 L 4 -4 L 2 -5 L -1 -5',
    'p': '-10 9 M -6 -5 L -6 16 M -6 -2 L -4 -4 L -2 -5 L 1 -5 L 3 -4 L 5 -2 L 6 1 L 6 3 L 5 6 L 3 8 L 1 9 L -2 9 L -4 8 L -6 6',
    'q': '-9 10 M 6 -5 L 6 16 M 6 -2 L 4 -4 L 2 -5 L -1 -5 L -3 -4 L -5 -2 L -6 1 L -6 3 L -5 6 L -3 8 L -1 9 L 2 9 L 4 8 L 6 6',
    'r': '-7 6 M -3 -5 L -3 9 M -3 1 L -2 -2 L 0 -4 L 2 -5 L 5 -5',
    's': '-8 9 M 6 -2 L 5 -4 L 2 -5 L -1 -5 L -4 -4 L -5 -2 L -4 0 L -2 1 L 3 2 L 5 3 L 6 5 L 6 6 L 5 8 L 2 9 L -1 9 L -4 8 L -5 6',
    't': '-5 7 M 0 -12 L 0 5 L 1 8 L 3 9 L 5 9 M -3 -5 L 4 -5',
    'u': '-9 10 M -5 -5 L -5 5 L -4 8 L -2 9 L 1 9 L 3 8 L 6 5 M 6 -5 L 6 9',
    'v': '-8 8 M -6 -5 L 0 9 M 6 -5 L 0 9',
    'w': '-11 11 M -8 -5 L -4 9 M 0 -5 L -4 9 M 0 -5 L 4 9 M 8 -5 L 4 9',
    'x': '-8 9 M -5 -5 L 6 9 M 6 -5 L -5 9',
    'y': '-8 8 M -6 -5 L 0 9 M 6 -5 L 0 9 L -2 13 L -4 15 L -6 16 L -7 16',
    'z': '-8 9 M 6 -5 L -5 9 M -5 -5 L 6 -5 M -5 9 L 6 9',
    '{': '-7 7 M 2 -16 L 0 -15 L -1 -14 L -2 -12 L -2 -10 L -1 -8 L 0 -7 L 1 -5 L 1 -3 L -1 -1 M 0 -15 L -1 -13 L -1 -11 L 0 -9 L 1 -8 L 2 -6 L 2 -4 L 1 -2 L -3 0 L 1 2 L 2 4 L 2 6 L 1 8 L 0 9 L -1 11 L -1 13 L 0 15 M -1 1 L 1 3 L 1 5 L 0 7 L -1 8 L -2 10 L -2 12 L -1 14 L 0 15 L 2 16',
    '|': '-4 4 M 0 -16 L 0 16',
    '}': '-7 7 M -2 -16 L 0 -15 L 1 -14 L 2 -12 L 2 -10 L 1 -8 L 0 -7 L -1 -5 L -1 -3 L 1 -1 M 0 -15 L 1 -13 L 1 -11 L 0 -9 L -1 -8 L -2 -6 L -2 -4 L -1 -2 L 3 0 L -1 2 L -2 4 L -2 6 L -1 8 L 0 9 L 1 11 L 1 13 L 0 15 M 1 1 L -1 3 L -1 5 L 0 7 L 1 8 L 2 10 L 2 12 L 1 14 L 0 15 L -2 16',
    '~': '-12 12 M -9 3 L -9 1 L -8 -2 L -6 -3 L -4 -3 L -2 -2 L 2 1 L 4 2 L 6 2 L 8 1 L 9 -1 M -9 1 L -8 -1 L -6 -2 L -4 -2 L -2 -1 L 2 2 L 4 3 L 6 3 L 8 2 L 9 -1 L 9 -3',
}

_NUMBERS = re.compile(r"-?\d+")


def parse_glyph(spec: str) -> tuple[int, int, list[list[tuple[int, int]]]]:
    """Unpack one entry: (left bearing, right bearing, strokes in font units).

    A ``M`` starts a stroke and every following pair is a point on it, exactly
    as the Hershey format defines it. The space glyph has no ``M`` at all and
    comes back with no strokes - it still has a width, which is the whole point
    of a space.
    """
    head, *rest = spec.split("M")
    left, right = (int(value) for value in head.split())
    strokes: list[list[tuple[int, int]]] = []
    for piece in rest:
        numbers = [int(value) for value in _NUMBERS.findall(piece)]
        points = list(zip(numbers[0::2], numbers[1::2]))
        if len(points) >= 2:
            strokes.append(points)
    return left, right, strokes


@dataclass
class TextStrokes:
    """What :func:`text_to_strokes` worked out, including what it could not do."""

    strokes: list[list[list[float]]] = field(default_factory=list)
    width_mm: float = 0.0
    height_mm: float = 0.0
    lines: int = 0
    glyphs: int = 0
    #: Characters with no glyph here, in the order they were met.
    missing: list[str] = field(default_factory=list)
    #: Advances that came from nothing but a space.
    spaces: int = 0
    #: True when these came from a real font's OUTLINES rather than Hershey's
    #: single lines. The difference matters downstream: an outline is a CLOSED
    #: contour, so it can be padded or pocketed but cannot be engraved by a
    #: point-on-curve sweep (a closed curve carries no such parameter - measured
    #: 2026-11-11).
    outline: bool = False
    #: Which face produced them, for the reply: '' for Hershey, else "family
    #: (path)".
    source: str = ""


def has_glyph(char: str) -> bool:
    return char in _GLYPHS


def supported() -> str:
    """The character set, as a readable run for an error message."""
    printable = "".join(sorted(_GLYPHS))
    return printable


def text_to_strokes(
    text: str,
    height_mm: float = 10.0,
    spacing_mm: float | None = None,
    origin: tuple[float, float] = (0.0, 0.0),
    line_spacing_mm: float | None = None,
) -> TextStrokes:
    """Turn text into polylines in millimetres, y up, baseline at ``origin``.

    ``height_mm`` is the CAP HEIGHT (the height of an ``H``), which is what a
    drawing means by "10 mm text". ``spacing_mm`` is the clear gap between one
    glyph's box and the next; ``None`` means 20 % of the cap height. A character
    that is not in the font is skipped and NAMED in ``missing`` - never silently
    replaced by a box, because a box would engrave as something the caller did
    not ask for.
    """
    if height_mm <= 0:
        # A coded error, not a bare ValueError: every refusal in this package
        # has to carry a code the caller can branch on (tests/test_argument_
        # envelopes.py enforces it).
        raise ArgumentRejected(
            f"height_mm must be positive; got {height_mm:g}.",
            code="BAD_ARGUMENT", stage="validate")
    scale = float(height_mm) / CAP_HEIGHT_UNITS
    spacing = (DEFAULT_SPACING_RATIO * float(height_mm)
               if spacing_mm is None else float(spacing_mm))
    line_step = (DEFAULT_LINE_RATIO * float(height_mm)
                 if line_spacing_mm is None else float(line_spacing_mm))
    origin_x, origin_y = float(origin[0]), float(origin[1])

    result = TextStrokes()
    lines = str(text).split("\n")
    result.lines = len(lines)
    widest = 0.0

    for line_index, line in enumerate(lines):
        pen_x = origin_x
        base_y = origin_y - line_index * line_step
        for char in line:
            spec = _GLYPHS.get(char)
            if spec is None:
                if char not in result.missing:
                    result.missing.append(char)
                continue
            left, right, strokes = parse_glyph(spec)
            for stroke in strokes:
                moved = []
                for x, y in stroke:
                    moved.append([
                        pen_x + (x - left) * scale,
                        base_y + (BASELINE_UNITS - y) * scale,
                    ])
                # A stroke whose points coincide after scaling would draw a
                # zero-length line, which catia_sketch_strokes refuses outright.
                keep = [moved[0]]
                for point in moved[1:]:
                    if (abs(point[0] - keep[-1][0]) > 1e-9
                            or abs(point[1] - keep[-1][1]) > 1e-9):
                        keep.append(point)
                if len(keep) >= 2:
                    result.strokes.append(keep)
            if not strokes:
                result.spaces += 1
            result.glyphs += 1
            pen_x += (right - left) * scale + spacing
            widest = max(widest, pen_x - origin_x - spacing)

    result.width_mm = max(0.0, widest)
    result.height_mm = float(height_mm)
    return result


# ── the outline face, for everything Hershey does not have ────────────────
#
# Hershey simplex covers ASCII 32-126 and nothing else. There is no public-domain
# SINGLE-LINE CJK face with useful coverage, so a Chinese word used to mean
# handing over point sets by hand - which is what the 2026-11-11 report did. What
# every machine already has is an OUTLINE font, and an outline is real geometry:
# it pads, it pockets, and its contour can be engraved. It is NOT what the ASCII
# path uses, because a groove a fraction of a millimetre deep cannot resolve the
# two walls of one stem - so the reply says which face was used and what that
# costs, rather than letting the caller assume.

#: Where a CJK face usually lives, in the order this prefers. A SANS face first:
#: the single-line look a caller wants is closest to it, and a song/serif face
#: has thick horizontals and thin verticals, which reads worse after engraving.
OUTLINE_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",          # Microsoft YaHei
    "C:/Windows/Fonts/msyh.ttf",
    "C:/Windows/Fonts/simhei.ttf",        # SimHei
    "C:/Windows/Fonts/Deng.ttf",          # DengXian
    "C:/Windows/Fonts/simsun.ttc",        # SimSun, last on Windows: serif
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
)

#: Chord error, in MILLIMETRES of the finished drawing, for flattening a glyph's
#: quadratic curves. 0.05 mm is a tenth of the 0.5 mm a drawing cares about and
#: keeps a CJK glyph near a hundred segments; tighter doubles the count for a
#: difference no 0.4 mm groove can show.
OUTLINE_FLATTEN_MM = 0.05

_FONT_CACHE: dict[tuple[str, int], object] = {}


def find_outline_font(preferred: str | None = None) -> tuple[str, int] | None:
    """``(path, face)`` of a usable outline font, or None if this machine has none.

    ``preferred`` is taken as given (the caller may know better than this list);
    its existence is checked, because a path that does not exist has to be
    reported rather than silently replaced by a system font.
    """
    import os

    if preferred:
        return (preferred, 0) if os.path.exists(preferred) else None
    for candidate in OUTLINE_FONT_CANDIDATES:
        if os.path.exists(candidate):
            return candidate, 0
    return None


def outline_font(preferred: str | None = None):
    """A parsed ``TrueTypeFont``, cached per path - parsing 20 MB is not free."""
    from catia_mcp.font_outline import TrueTypeFont

    found = find_outline_font(preferred)
    if found is None:
        # One sentence, no line breaks: every path is one clause, and a message
        # that wraps has to be re-read to be believed.
        searched = ", ".join(OUTLINE_FONT_CANDIDATES)
        raise ArgumentRejected(
            "No outline font found on this machine, so text outside ASCII cannot "
            "be drawn. Looked for these, in this order: " + searched + ". "
            "Pass font_path to name one: a TrueType .ttf/.ttc with CJK coverage "
            "(a CFF/OpenType .otf is refused - its outlines are PostScript, not "
            "quadratic). Nothing was drawn.",
            code="NO_OUTLINE_FONT", stage="validate")
    path, face = found
    key = (path, face)
    if key not in _FONT_CACHE:
        try:
            _FONT_CACHE[key] = TrueTypeFont(path, face)
        except Exception as error:  # noqa: BLE001 - the reader's own message is the answer
            raise ArgumentRejected(
                f"Cannot read the font '{path}': {error}. Nothing was drawn.",
                code="BAD_OUTLINE_FONT", stage="validate") from error
    return _FONT_CACHE[key]


def text_to_outlines(
    text: str,
    height_mm: float = 10.0,
    font_path: str | None = None,
    spacing_mm: float | None = None,
    origin: tuple[float, float] = (0.0, 0.0),
    line_spacing_mm: float | None = None,
    flatten_mm: float = OUTLINE_FLATTEN_MM,
) -> TextStrokes:
    """Text as CLOSED CONTOURS in millimetres, y up, baseline at ``origin``.

    ``height_mm`` is the CAP HEIGHT, exactly as in :func:`text_to_strokes`, so a
    caller who switches faces does not have to relearn the argument. An ideograph
    is taller than a Latin capital - it fills about 0.88 of the em box against a
    cap height of about 0.70 - so 中文 drawn at height 10 measures roughly 12.4 mm
    tall. That is not a bug to hide: the reply prints the box the text actually
    occupies, and the caller who wants 10 mm characters passes height 8.
    """
    if height_mm <= 0:
        raise ArgumentRejected(
            f"height_mm must be positive; got {height_mm:g}.",
            code="BAD_ARGUMENT", stage="validate")
    font = outline_font(font_path)
    scale = float(height_mm) / float(font.cap_height or font.units_per_em)
    spacing = (DEFAULT_SPACING_RATIO * float(height_mm)
               if spacing_mm is None else float(spacing_mm))
    line_step = (DEFAULT_LINE_RATIO * float(height_mm)
                 if line_spacing_mm is None else float(line_spacing_mm))
    origin_x, origin_y = float(origin[0]), float(origin[1])
    flatten_units = max(1.0, float(flatten_mm) / scale) if scale > 0 else 1.0

    result = TextStrokes(outline=True,
                         source=f"{font.family or 'unnamed'} ({font.path})")
    lines = str(text).split("\n")
    result.lines = len(lines)
    widest = 0.0
    for line_index, line in enumerate(lines):
        pen_x = origin_x
        base_y = origin_y - line_index * line_step
        for char in line:
            glyph = font.glyph(ord(char), flatten=flatten_units) if char.strip() else None
            if char == " ":
                result.spaces += 1
                pen_x += font.advance(font._glyph_index(0x20)) * scale + spacing
                continue
            if glyph is None or not glyph.contours:
                if char not in result.missing:
                    result.missing.append(char)
                continue
            for contour in glyph.contours:
                moved = [[pen_x + x * scale, base_y + y * scale] for x, y in contour]
                keep = [moved[0]]
                for point in moved[1:]:
                    if (abs(point[0] - keep[-1][0]) > 1e-9
                            or abs(point[1] - keep[-1][1]) > 1e-9):
                        keep.append(point)
                if len(keep) >= 3:
                    result.strokes.append(keep)
            result.glyphs += 1
            pen_x += glyph.advance * scale + spacing
            widest = max(widest, pen_x - origin_x - spacing)
    result.width_mm = max(0.0, widest)
    result.height_mm = float(height_mm)
    return result
