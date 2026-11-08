"""Colour primitives, in one place.

``catia_set_visibility`` and the colour tools both drive
``Selection.VisProperties``, and the two halves of that interface behave nothing
like each other on this build. Measured on V5-6R2020 B30, 2026-11-08:

* ``SetRealColor(R, G, B)`` is REFUSED with 无效的参数数目 ("invalid number of
  arguments"). The build wants a fourth value - and it is **not a dummy**: even
  values (0, 2) store the colour and leave the picture untouched, odd ones (1, 3)
  apply it. Measured on B30 2026-11-08 by colouring a component red and sampling
  the screenshot after each: with flag 0 the histogram was byte-identical to the
  uncoloured one, with flag 1 the solid came out (174, 24, 25). Passing 0 -
  which reads back the new colour and looks like success - was this feature's
  first version, and it produced a gray assembly with a green property.
  :func:`apply_colour` therefore uses 1. Passing three is the documented
  call and it does not work.
* ``GetRealColor()`` returns FOUR values and the RGB is the LAST THREE: after
  ``SetRealColor(220, 30, 30, 0)`` it answers ``(0, 220, 30, 30)``. Slicing
  ``[0:3]`` - the obvious reading - reports black for every colour. Same shape as
  ``GetShow()`` returning a 2-tuple with the flag at [1], which cost a release.
* An untouched part reads ``(0, 210, 210, 255)``, i.e. RGB (210, 210, 255) - not
  the white one would guess. So "this value equals the uncoloured default" is
  evidence and not proof: the interface reports what is DISPLAYED, and a colour
  deliberately set to that exact value reads identically.
* ``Selection.Add(part)`` (the Part object rather than a body) is ACCEPTED and
  reads back (0, 0, 0): the Part node is not a colour target. Use the body.

The primitives live here rather than in ``tools/appearance.py`` because
``tools/assembly.py`` needs :func:`read_rgb` for its component listing, and the
tool modules already import each other where they have to - a second copy of the
four-argument signature is exactly how the ``GetShow`` index stayed wrong.
"""
from __future__ import annotations

import colorsys
from typing import Any

from catia_mcp.results import ArgumentRejected

#: Distinct at a glance on a shaded solid; the first entries are the ones a
#: two- or three-part assembly gets.
PALETTE = (
    "#E4572E",  # orange-red
    "#2E86AB",  # steel blue
    "#76B041",  # green
    "#F5C518",  # yellow
    "#8E44AD",  # purple
    "#16A085",  # teal
    "#C0392B",  # dark red
    "#2874A6",  # dark blue
    "#B9770E",  # ochre
    "#5D6D7E",  # slate
    "#D81B60",  # magenta
    "#7CB342",  # light green
)

NAMED_COLOURS = {
    "red": (220, 30, 30),
    "green": (60, 160, 60),
    "blue": (30, 90, 220),
    "yellow": (245, 200, 20),
    "orange": (230, 120, 20),
    "purple": (140, 60, 175),
    "grey": (128, 128, 128),
    "gray": (128, 128, 128),
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "cyan": (0, 200, 200),
    "magenta": (220, 20, 150),
    "brown": (140, 90, 50),
    "pink": (240, 150, 180),
    "teal": (20, 160, 140),
    "navy": (20, 40, 120),
    "lime": (150, 220, 30),
    "silver": (190, 190, 190),
    "gold": (212, 175, 55),
}

#: What an untouched PART BODY reads back as (measured on B30). Not white, which
#: is what the reference implies.
UNCOLOURED = (210, 210, 255)

#: What an untouched COMPONENT reads back as, and it is a different value from the
#: one above - measured on B30 2026-11-08 with the same part in the same session:
#: the body in its own document answered (210, 210, 255) while all three instances
#: in the assembly answered (255, 255, 255).
#:
#: 0.3.79 compared BOTH against ``UNCOLOURED``, so every instance in an uncoloured
#: assembly counted as "already carried a colour" - a 25-instance assembly with 11
#: parts answered "11 already carried a colour, which was overwritten" about files
#: that had never been coloured (reported 2026-11-08, P1-1). Two contexts, two
#: defaults, and the tools now say which one they compared against.
UNCOLOURED_INSTANCE = (255, 255, 255)

FORMS = "'#RRGGBB' (or '#RGB'), a colour name, [r, g, b], or \"r,g,b\""


def hex_of(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def parse_colour(value: Any) -> tuple[int, int, int]:
    """Every accepted form, validated before anything is written."""
    if isinstance(value, (list, tuple)):
        numbers = list(value)
        if len(numbers) != 3:
            raise ArgumentRejected(
                f"A colour needs exactly three channel values, not {len(numbers)}. "
                f"Use {FORMS}."
            )
    elif isinstance(value, str):
        text = value.strip()
        if text.lower() in NAMED_COLOURS:
            return NAMED_COLOURS[text.lower()]
        if "," in text:
            numbers = [part for part in (p.strip() for p in text.split(",")) if part]
            if len(numbers) != 3:
                raise ArgumentRejected(f"'{value}' does not hold three channels. Use {FORMS}.")
        else:
            digits = text[1:] if text.startswith("#") else text
            if len(digits) == 3 and all(c in "0123456789abcdefABCDEF" for c in digits):
                digits = "".join(c * 2 for c in digits)
            if len(digits) != 6 or any(c not in "0123456789abcdefABCDEF" for c in digits):
                raise ArgumentRejected(
                    f"'{value}' is not a colour name or a hex colour. Use {FORMS}; "
                    f"names: {', '.join(sorted(NAMED_COLOURS))}."
                )
            return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))
    else:
        raise ArgumentRejected(f"A colour must be a string or three numbers. Use {FORMS}.")

    try:
        channels = [int(round(float(n))) for n in numbers]
    except (TypeError, ValueError) as error:
        raise ArgumentRejected(
            f"A colour channel must be a number, got {numbers!r}. Use {FORMS}."
        ) from error
    for channel in channels:
        if not 0 <= channel <= 255:
            raise ArgumentRejected(f"Colour channels run 0-255, got {channels}. Use {FORMS}.")
    return (channels[0], channels[1], channels[2])


def generated_colour(index: int) -> tuple[int, int, int]:
    """A colour for group N beyond the palette: golden-angle hue, nothing repeats."""
    hue = (index * 0.618033988749895) % 1.0
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.62, 0.90)
    return (int(red * 255), int(green * 255), int(blue * 255))


def read_rgb(selection: Any) -> tuple[int, int, int] | None:
    """RGB of whatever is currently selected - None if it cannot be read.

    ``GetRealColor()`` gives four values here and the RGB is the last three.
    """
    try:
        values = tuple(int(v) for v in selection.VisProperties.GetRealColor())
    except Exception:  # noqa: BLE001 - nothing readable, or not a colour target
        return None
    if len(values) < 3:
        return None
    return (values[-3], values[-2], values[-1])


#: The fourth argument of ``SetRealColor``. It is a status flag whose EVEN values
#: store the colour without applying it - the property then reads back correctly
#: while the view stays gray, which is the worst possible failure for a tool whose
#: whole purpose is a picture. Measured on B30 2026-11-08: 0 and 2 changed nothing,
#: 1 and 3 coloured the solid. There is no enum name to look up (the typelib does
#: not expose one through late binding), so the measured value lives here.
APPLY = 1


def apply_colour(selection: Any, node: Any,
                 rgb: tuple[int, int, int]) -> tuple[int, int, int] | None:
    """Colour one node on the current selection and read it straight back.

    Four arguments with the APPLY flag - see the module docstring, and note that
    the read-back alone cannot tell a stored colour from a displayed one.
    """
    selection.Clear()
    selection.Add(node)
    selection.VisProperties.SetRealColor(rgb[0], rgb[1], rgb[2], APPLY)
    return read_rgb(selection)


def uncoloured_note(rgb: tuple[int, int, int] | None,
                    instance: bool = False) -> str:
    """The sentence that keeps "reads as the default" from becoming "unset".

    ``instance`` picks WHICH default: a component in an assembly reads
    (255, 255, 255), a body in its part document (210, 210, 255). Comparing an
    instance against the body default is what produced 0.3.79's P1-1.
    """
    default = UNCOLOURED_INSTANCE if instance else UNCOLOURED
    if rgb != default:
        return ""
    where = "uncoloured INSTANCE default" if instance else "uncoloured default"
    return (f"  (matches this build's {where} {hex_of(default)} - "
            "evidence, not proof: a colour set to that exact value reads the same)")


def is_default(rgb: tuple[int, int, int] | None, instance: bool = False) -> bool:
    """True when ``rgb`` is the measured uncoloured default for that context."""
    return rgb == (UNCOLOURED_INSTANCE if instance else UNCOLOURED)
