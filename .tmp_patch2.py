import pathlib
p = pathlib.Path("catia_mcp/tools/sketcher.py")
s = p.read_text(encoding="utf-8")

# 1) __init__ : drawn record
s = s.replace(
"""        # Endpoints of the profile as drawn, in sketch coordinates. Kept
        # because the COM API will not give them back: see _draw_arc.
        self._profile_ends: list[tuple[float, float]] = []""",
"""        # Endpoints of the profile as drawn, in sketch coordinates. Kept
        # because the COM API will not give them back: see _draw_arc.
        self._profile_ends: list[tuple[float, float]] = []
        # What this session drew into each sketch, by sketch name. The only
        # read-back that survives closing a sketch, because it comes from the
        # arguments rather than from CATIA (see _get_geometry's note).
        self._drawn: dict[str, list[dict[str, Any]]] = {}""")

# 2) record helper + summary, next to _note_ends
s = s.replace(
"""    def _note_ends(self, *points: tuple[float, float]) -> None:
        self._profile_ends.extend((float(x), float(y)) for x, y in points)""",
"""    def _note_ends(self, *points: tuple[float, float]) -> None:
        self._profile_ends.extend((float(x), float(y)) for x, y in points)

    def _note_element(self, kind: str, **params: Any) -> None:
        \"\"\"Remember what was drawn, for the profile echo at close time.\"\"\"
        name = "?"
        try:
            name = str(self._active_sketch.Name)
        except Exception:  # noqa: BLE001 - the record is still useful
            pass
        self._drawn.setdefault(name, []).append({"kind": kind, **params})

    def _profile_summary(self) -> str:
        \"\"\"Echo the drawn profile: counts, size and total outline length.

        This is the counter to "the profile was wrong and nothing said so"
        (reported 2026-10-24, where two missing segments showed up only as a
        32 % volume error). The numbers come from the drawing calls, since
        CATIA's own coordinates cannot be read back.
        \"\"\"
        import math

        ends = self._profile_ends
        drawn = []
        try:
            drawn = self._drawn.get(str(self._active_sketch.Name), [])
        except Exception:  # noqa: BLE001
            pass
        if not ends and not drawn:
            return ""
        counts: dict[str, int] = {}
        length = 0.0
        for item in drawn:
            counts[item["kind"]] = counts.get(item["kind"], 0) + 1
            if item["kind"] == "line":
                length += math.hypot(item["x2"] - item["x1"], item["y2"] - item["y1"])
            elif item["kind"] in ("arc", "circle"):
                length += item["radius"] * abs(item["sweep_rad"])
            elif item["kind"] == "rectangle":
                length += 2 * (abs(item["x2"] - item["x1"]) + abs(item["y2"] - item["y1"]))
            elif item["kind"] == "spline":
                length += item.get("chord", 0.0)
        parts = []
        if counts:
            parts.append(", ".join(f"{n} {kind}(s)" for kind, n in sorted(counts.items())))
        if ends:
            xs = [p[0] for p in ends]
            ys = [p[1] for p in ends]
            parts.append("bbox (%.4f, %.4f) .. (%.4f, %.4f)"
                         % (min(xs), min(ys), max(xs), max(ys)))
        if length:
            parts.append("outline %.3f mm" % length)
        if not parts:
            return ""
        return " Profile: " + "; ".join(parts) + ".\"\"\"")

# 3) close_sketch: add the summary before / with the warning
s = s.replace(
"""        self.conn.refresh_display()
        warning = self._profile_warning()
        self._profile_ends = []
        return (
            f"Sketch '{name}' closed. You can now apply Part Design features "
            "(pad, pocket, user pattern on its points, etc.)." + warning
        )""",
"""        self.conn.refresh_display()
        summary = self._profile_summary()
        warning = self._profile_warning()
        self._profile_ends = []
        return (
            f"Sketch '{name}' closed. You can now apply Part Design features "
            "(pad, pocket, user pattern on its points, etc.)."
            + summary + warning
        )""")
p.write_text(s, encoding="utf-8", newline="\n")
print("patched init/close")
