"""Export tools for CATIA V5.

Export to STEP, IGES, STL, 3DXML, and other formats.
Also includes screenshot capture.
"""

from __future__ import annotations

import json
import math
import os
import time
from typing import Any

from catia_mcp.connection import CATIAConnection
from catia_mcp.results import ArgumentRejected, CodedError, PreconditionRejected

# CATIA export format identifiers
FORMAT_MAP = {
    "step": "stp",
    "stp": "stp",
    "iges": "igs",
    "igs": "igs",
    "stl": "stl",
    "3dxml": "3dxml",
    "wrl": "wrl",
    "vrml": "wrl",
    "pdf": "pdf",
    "cgr": "cgr",
}


# Export formats that need something to TESSELLATE, guarded because an export
# with nothing in it has no answer worth waiting for: one probe script that ran
# it hung for five minutes with no output and had to be killed (2026-11-04, not
# reproduced). Measured the same day: a part holding ONE SPLINE exported an STL
# fine, so a curve-only document is not the case this guards against.
MESH_FORMATS = {"stl", "3dxml", "wrl", "vrml", "cgr"}


class ExportTools:
    """Tools for exporting CATIA V5 data to external formats."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_export",
                "description": (
                    "Export the active document to a file. "
                    "Supported formats: STEP (.stp), IGES (.igs), STL (.stl), "
                    "3DXML (.3dxml), VRML (.wrl), PDF (2D drawings)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": (
                                "Output file path. The format is determined by the extension. "
                                "Example: 'C:/export/my_part.stp'"
                            ),
                        },
                        "format": {
                            "type": "string",
                            "description": (
                                "Export format (optional if file extension is provided). "
                                "One of: step, iges, stl, 3dxml, vrml"
                            ),
                            "enum": ["step", "iges", "stl", "3dxml", "vrml"],
                        },
                    },
                    "required": ["file_path"],
                },
            },
            {
                "name": "catia_screenshot",
                "description": (
                    "Capture a screenshot of the current 3D view and save as image file. "
                    "Supports JPG, BMP, TIFF (CATIA V5 cannot capture PNG; a .png path "
                    "is saved as .jpg instead). "
                    "THE SIZE IS THE WINDOW'S, AND THE WINDOW CAN BE RESIZED: pass "
                    "width/height and this tool sets Window.Width/Height before the "
                    "capture and puts them back afterwards. Measured on B30 "
                    "2026-11-19: 600x877, 1100x900 and 1600x900 requested gave files "
                    "of exactly those sizes (the 0.3.103 note that said otherwise was "
                    "wrong - it read the client area of a maximized window and "
                    "concluded the setter did nothing). width/height of 0 (the "
                    "default) leave the window alone and the reply reports the size "
                    "actually written, read from the file header. A maximized window "
                    "ignores the setter, and a size larger than the screen is clamped; "
                    "the reply names whichever happened. "
                    "THE PICTURE IS NOT A CLEAN RENDER: it contains the specification "
                    "tree and the axis triad, and neither can be suppressed from "
                    "automation - no viewer member controls them, and "
                    "`Application.StartCommand` does NOT validate the command name (an "
                    "unknown one is accepted and then opens a modal 未知命令 dialog on "
                    "the automation thread; re-measured 2026-11-19, it hangs the "
                    "caller). Hide the tree in the UI (F3) before calling this if the "
                    "picture is for a report. "
                    "crop=[x, y, w, h] cuts the written image afterwards (needs "
                    "Pillow; without it the tool says so instead of pretending), which "
                    "is the only way to drop the tree or the triad from a picture."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Output image path (e.g., 'C:/screenshots/part.png')",
                        },
                        "zoom": {
                            "type": "number",
                            "default": 1.0,
                            "description": (
                                "Multiplier on the camera's current zoom - 2.0 makes "
                                "the part twice as close, 0.5 twice as far. 1.0 (the "
                                "default) changes nothing. This is the closest thing "
                                "to a 'crop' that exists on this build: there is no "
                                "capture region, and cutting the image would need a "
                                "JPEG decoder. Measured on B30 2026-11-15: setting "
                                "Viewpoint3D.Zoom reads back the exact value asked "
                                "for, the object covered 24 668 / 35 989 / 41 588 "
                                "pixels at x0.6 / x1 / x1.6 of the same window, and "
                                "the zoom is RESTORED after the capture."
                            ),
                        },
                        "width": {
                            "type": "integer",
                            "description": (
                                "Image width in pixels. The tool resizes the CATIA "
                                "window to match before capturing and restores it "
                                "afterwards (measured on B30 2026-11-19: 600 / 1100 / "
                                "1600 asked, 600x877 / 1100x900 / 1600x900 written). "
                                "0 = leave the window as it is and report the size "
                                "that was written."
                            ),
                            "default": 0,
                        },
                        "height": {
                            "type": "integer",
                            "description": (
                                "Image height in pixels, same rule as 'width'; 0 = "
                                "leave the window's height alone."
                            ),
                            "default": 0,
                        },
                        "crop": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 4,
                            "maxItems": 4,
                            "description": (
                                "[x, y, width, height] in pixels from the TOP-LEFT of "
                                "the captured image, applied to the written file with "
                                "Pillow. This is the only way to take the "
                                "specification tree or the axis triad out of a "
                                "picture: CATIA exposes no capture region and no "
                                "switch for either. The reply reports the cropped "
                                "size. "
                                "NOBODY CAN COMPUTE x FOR YOU, and that is measured, "
                                "not assumed (B30 2026-11-21, the 2026-11-21 "
                                "report's P2-19): no member reports the viewport "
                                "rectangle or the tree's width - 18 plausible names "
                                "on Application / Window / Viewer3D "
                                "(SpecTreeWidth, SpecTreeVisible, "
                                "DisplaySpecifications, ViewportWidth, Layout, ...) "
                                "all answer 'not found'; Viewer3D.Width/Height "
                                "answer the CAPTURE size (300x400 on a capture that "
                                "was exactly 300x400), Window.Width/Height answer "
                                "116/0 for that same window, and Viewpoint3D exposes "
                                "no Eye/SightDirection/UpDirection (only Zoom, "
                                "ProjectionMode, FieldOfView), so the model's box "
                                "cannot be projected into pixels either. Hiding the "
                                "solid and diffing two captures does not isolate it: "
                                "with the body hidden the whole viewport is redrawn "
                                "(the ground grid goes with it) and the two frames "
                                "differ from y=143 to the bottom edge. So the tree "
                                "band is YOUR decision - [tree_width, 0, "
                                "width - tree_width, height] - and it does change "
                                "with the language pack and the font size."
                            ),
                        },
                        "clean": {
                            "type": ["boolean", "string"],
                            "default": False,
                            "description": (
                                "Leave construction geometry out of the picture: "
                                "hide it, capture, then put every visibility flag "
                                "back. TWO MODES, because two kinds of part need "
                                "cleaning and only one of them has geometrical "
                                "sets. "
                                "false (default): nothing is changed. "
                                "true or 'sets': hide the GEOMETRICAL SETS, "
                                "keeping whatever carries the solid visible - right "
                                "for a part built from GSD surfaces, and what "
                                "clean=true has meant since 0.3.53. "
                                "'solid_only': the sets AND THE SKETCHES INSIDE THE "
                                "SOLID BODIES. Pass this for a part whose features "
                                "are sketch-based: clean=true answers 'this part "
                                "has no geometrical sets, so nothing was hidden' "
                                "there while every profile curve sits on top of the "
                                "solid (the 2026-11-14 report's P1-3). "
                                "WHAT IS DELIBERATELY NOT HIDDEN, measured on B30 "
                                "2026-11-14 with four captures compared by hash: "
                                "the datum planes (hiding the 3 origin planes "
                                "changes nothing at all) and the elements inside "
                                "the bodies - body.HybridShapes there holds "
                                "['Datum_xy_20', 'Body'], so hiding that collection "
                                "switches the SOLID ITSELF off and the window shows "
                                "only the grid; setting the loft's own flag back "
                                "does not help. Hiding only the sketches leaves the "
                                "solid fully visible with its profiles gone. "
                                "THE TREE IS STILL IN THE PICTURE: no automation "
                                "member hides it (Viewer3D exposes 58 members on B30 "
                                "and no HideTree/ShowTree, measured 2026-11-10) - "
                                "press F3 in the UI before calling this for a report "
                                "image."),
                        },
                    },
                    "required": ["file_path"],
                },
            },
            {
                "name": "catia_set_view",
                "description": (
                    "Set the 3D view orientation, then fit all. CATIA axes: X right, "
                    "Y depth, Z up. front looks along +Y (you see X right and Z up), "
                    "top looks down -Z (X right, Y up), right looks along -X. The "
                    "table was wrong through 0.3.49 - front/back and top/bottom were "
                    "swapped, so 'front' gave a top-down picture; fixed and verified "
                    "with one screenshot per view on B30 2026-11-03. Unknown names "
                    "are refused, never guessed. THE NAMES ARE IN THE DOCUMENT'S "
                    "ABSOLUTE AXES, not the model's: 'front' is look-along-minus-Y "
                    "with +Z up whatever the part's own orientation, so a part "
                    "built rotated in the tree (or a component placed with a "
                    "transform) will not line up with any of them. "
                    "PARALLEL PROJECTION BY DEFAULT, and that is the 0.3.121 fix. "
                    "Two 0.3.120 reports said 'front' was tilted 15-20 degrees with "
                    "the bottom of the bottle visible; measured on B30 2026-11-13, "
                    "the sight and up vectors were exactly right (read back after "
                    "the Put) and the culprit was ProjectionMode, which reads 0 = "
                    "PERSPECTIVE on this install - a perspective camera framing a "
                    "60x40x4 plate looks down on it, so the plate shows its top "
                    "face in a 'front' view. With ProjectionMode=1 the same plate "
                    "is a clean elevation (two captures differing in nothing else, "
                    "scripts/b30_v0423_probe_view2.py). So this now forces "
                    "parallel unless projection='perspective' is passed, and it "
                    "READS THE CAMERA BACK (GetSightDirection/GetUpDirection through "
                    "SystemService.Evaluate - the by-ref array does not marshal "
                    "late-bound) and prints what CATIA actually holds, so an "
                    "ignored Put says so instead of leaving a plausible picture. "
                    "For a view none of the seven names describe - looking along a "
                    "face normal, or down a hole - pass direction=[x,y,z] and "
                    "optionally up=[x,y,z]."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "view": {
                            "type": "string",
                            "description": (
                                "View orientation. Ignored when 'direction' is "
                                "given."
                            ),
                            "enum": [
                                "front", "back", "top", "bottom",
                                "left", "right", "isometric",
                            ],
                        },
                        "direction": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": (
                                "Sight direction [x, y, z] in the document's axes - "
                                "the way the CAMERA LOOKS, so [0, 1, 0] is the same "
                                "as view='front'. Use it to look along a face normal "
                                "or a hole axis."
                            ),
                        },
                        "up": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": (
                                "Screen up vector [x, y, z]. Optional with "
                                "'direction' (Z is used unless the sight is within "
                                "26 degrees of vertical, then Y). It must not be "
                                "parallel to the sight."
                            ),
                        },
                        "projection": {
                            "type": "string",
                            "enum": ["parallel", "perspective"],
                            "default": "parallel",
                            "description": (
                                "'parallel' (default) is an orthographic elevation, "
                                "which is what 'is this edge vertical' needs. "
                                "'perspective' restores CATIA's default 0 - offered "
                                "for a presentation shot, never for a measurement."
                            ),
                        },
                    },
                },
            },
            {
                "name": "catia_fit_all",
                "description": "Fit all geometry in the current 3D view (zoom to fit).",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        match tool_name:
            case "catia_export":
                return self._export(arguments["file_path"], arguments.get("format"))
            case "catia_screenshot":
                return self._screenshot(
                    arguments["file_path"],
                    arguments.get("width", 0),
                    arguments.get("height", 0),
                    self._clean_mode(arguments.get("clean")),
                    arguments.get("zoom", 1.0),
                    arguments.get("crop"),
                )
            case "catia_set_view":
                return self._set_view(
                    arguments.get("view"),
                    arguments.get("direction"),
                    arguments.get("up"),
                    str(arguments.get("projection") or "parallel"),
                )
            case "catia_fit_all":
                return self._fit_all()
            case _:
                raise ArgumentRejected(f"Unknown export tool: {tool_name}")

    def _assert_has_mesh_geometry(self, doc: Any, fmt_key: str) -> None:
        """Refuse a mesh export when the part has nothing to tessellate.

        This is the one check that can be made without walking topology: whether
        the part has ANY solid shape or ANY GSD element at all.

        What is measured, and what is not: on B30 2026-11-04 an EMPTY part was
        refused here, and a part holding exactly one spline exported an STL
        without complaint - so a curve-only document is NOT the failure this
        guard is about. The guard exists because one probe script exported an
        empty part, hung for five minutes with no output and was killed, and
        that event has not been reproduced; the guard stops the pointless call,
        not a dialog it can prove.
        """
        try:
            part = doc.Part
        except Exception:  # noqa: BLE001 - a product or a drawing is not this case
            return
        solids = 0
        try:
            for index in range(1, part.Bodies.Count + 1):
                try:
                    solids += part.Bodies.Item(index).Shapes.Count
                except Exception:  # noqa: BLE001 - one unreadable body is not zero
                    continue
        except Exception:  # noqa: BLE001
            return
        gsd = 0
        try:
            for index in range(1, part.HybridBodies.Count + 1):
                try:
                    gsd += part.HybridBodies.Item(index).HybridShapes.Count
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            return
        if solids:
            return
        if not gsd:
            raise PreconditionRejected(
                f"Nothing to export as {fmt_key}: this part has no solid shape and "
                "no GSD element, so there is nothing to tessellate. Exporting it is "
                "pointless at best - one probe script that did it hung for five "
                "minutes with no output and had to be killed (2026-11-04, not "
                "reproduced since), which is the only reason this guard exists. "
                "This does NOT mean CATIA refuses such a document loudly: a part "
                "holding a single spline exported an STL fine on the same machine "
                "the same day, so a curve-only part is not stopped here."
            )
        # Everything is present. Whether it is VISIBLE cannot be checked cheaply
        # and correctly at once: HybridShape.Visible is not readable through
        # automation at all ("AttributeError: Item.Visible", measured on B30
        # 2026-11-04; writing it is refused too, which is why catia_set_visibility
        # goes through the selection's VisProperties). Reading it per element
        # means one selection round trip apiece. So the empty export is caught
        # AFTER the call, by file size - see _export.

    def _export(self, file_path: str, fmt: str | None = None) -> str:
        self.conn.ensure_connected()
        doc = self.conn.active_document

        # Determine format from extension if not specified
        if fmt is None:
            ext = os.path.splitext(file_path)[1].lstrip(".").lower()
            fmt = ext

        fmt_key = fmt.lower()
        if fmt_key not in FORMAT_MAP:
            supported = ", ".join(sorted(set(FORMAT_MAP.keys())))
            raise ArgumentRejected(
                f"Unsupported export format: '{fmt}'. Supported: {supported}"
            )

        # Ensure output directory exists
        output_dir = os.path.dirname(file_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        if fmt_key in MESH_FORMATS:
            self._assert_has_mesh_geometry(doc, fmt_key)

        # CATIA V5 export via SaveAs with format specification.
        # Normalize separators: ExportData rejects forward-slash paths (E_FAIL).
        #
        # ExportData runs CATIA's FILE-ALERT path - it is a save - so a target that
        # already exists raises a modal "要覆盖它吗?" nobody can answer on the
        # automation thread, and the call never returns. Measured on B30
        # 2026-11-04: the export hung until the dialog was clicked, which is how
        # this was found (measurement._export_stl has switched the alerts off
        # since 0.3.23; this tool never did). Restored in `finally` because
        # leaving it off would make the NEXT interactive save fail silently.
        alert_state = None
        try:
            alert_state = self.conn.app.DisplayFileAlerts
            self.conn.app.DisplayFileAlerts = False
        except Exception:  # noqa: BLE001 - not every build exposes it
            alert_state = None
        try:
            doc.ExportData(os.path.normpath(file_path), FORMAT_MAP[fmt_key])
        finally:
            if alert_state is not None:
                try:
                    self.conn.app.DisplayFileAlerts = alert_state
                except Exception:  # noqa: BLE001
                    pass

        # An export with nothing VISIBLE in it succeeds and writes an empty mesh -
        # 37 bytes, 0 triangles - with no error at all (reported on B32 2026-11-04:
        # every geometrical set hidden, "ok" returned, and the caller spent two
        # rounds believing the file held geometry). Deleting it is part of the
        # answer: a caller that ignores the exception must not be left holding a
        # file that looks exported.
        if fmt_key in MESH_FORMATS:
            written = os.path.getsize(file_path) if os.path.exists(file_path) else 0
            if written < 128:
                try:
                    os.remove(file_path)
                except OSError:  # noqa: BLE001 - the message matters, not the file
                    pass
                raise CodedError(
                    f"The {fmt_key} came out EMPTY ({written} bytes, no triangles), "
                    "so this part has no VISIBLE geometry to tessellate - elements "
                    "that exist but are all hidden export as nothing, and CATIA "
                    "reports success while doing it. Make at least one element or "
                    "body visible (catia_set_visibility) and retry, or export "
                    "STEP/IGES, which carry the tree rather than the visible skin. "
                    "The empty file was removed rather than left behind."
                , code="NO_VISIBLE_GEOMETRY", stage="execute")

        size_info = ""
        if os.path.exists(file_path):
            size_bytes = os.path.getsize(file_path)
            if size_bytes > 1024 * 1024:
                size_info = f" ({size_bytes / (1024*1024):.1f} MB)"
            elif size_bytes > 1024:
                size_info = f" ({size_bytes / 1024:.1f} KB)"
            else:
                size_info = f" ({size_bytes} bytes)"

        # WHY THIS ACCEPTS A NON-ASCII FILE NAME AND catia_save_document DOES NOT
        # (the 2026-11-14 report's P1-5 called the difference an inconsistency
        # and asked for one policy). They are two different CATIA APIs, not two
        # opinions: ExportData writes a translation through the file system, while
        # SaveAs goes through the DOCUMENT layer, which is ANSI on this build in
        # both directions - measured on B30 2026-11-07, Documents.Open refuses a
        # Chinese .CATPart name and leaves a modal dialog, so an ASCII save
        # renamed afterwards is an unopenable file. The report's own export
        # (160 847 bytes, Chinese name intact) reproduced here. Nothing to unify:
        # refusing here would break a call that works, and allowing it there would
        # produce files CATIA cannot reopen.
        non_ascii = ""
        if not os.path.basename(str(file_path)).isascii():
            non_ascii = (" The file name is not ASCII and that is fine HERE - this "
                         "goes through ExportData, not the document layer, so the "
                         ".CATPart naming limit does not apply to it (exported files "
                         "are opened by other software). Do not read it as a licence "
                         "for catia_save_document: that one is refused, because this "
                         "build cannot reopen a non-ASCII .CATPart name.")
        return (f"Exported to {file_path}{size_info} (format: {fmt_key.upper()})"
                + non_ascii)

    @staticmethod
    def _image_size(path: str) -> str | None:
        """'WxH' read from the file's own header, or None.

        Read from the bytes, not from the request: `CaptureToFile` writes the
        3D window's client area and there is no API to ask for another size
        (measured on B30 2026-11-03: 900x900 requested, 300x400 written, and
        setting Window.Width/Height changed nothing). The old reply printed the
        REQUESTED size, which is how a caller ends up measuring a picture that is
        not the resolution he asked for (reported 2026-11-03, 5.1).
        """
        try:
            with open(path, "rb") as handle:
                head = handle.read(32)
                if head[:2] == b"\xff\xd8":          # JPEG
                    handle.seek(2)
                    while True:
                        marker = handle.read(2)
                        if len(marker) < 2:
                            return None
                        if marker[0] != 0xFF:
                            continue
                        if marker[1] in (0xC0, 0xC1, 0xC2, 0xC3):
                            handle.read(3)
                            height, width = (
                                int.from_bytes(handle.read(2), "big"),
                                int.from_bytes(handle.read(2), "big"),
                            )
                            return f"{width}x{height}"
                        length = int.from_bytes(handle.read(2), "big")
                        handle.seek(length - 2, 1)
                if head[:2] == b"BM":                # BMP
                    handle.seek(18)
                    width = int.from_bytes(handle.read(4), "little", signed=True)
                    height = int.from_bytes(handle.read(4), "little", signed=True)
                    return f"{width}x{abs(height)}"
        except Exception:  # noqa: BLE001 - an unreadable header is not a failure
            return None
        return None

    _CLEAN_MODES = ("sets", "solid_only")

    @classmethod
    def _clean_mode(cls, value: Any) -> str:
        """``clean`` as a mode name, or "" for "leave the picture alone".

        ``true`` keeps meaning what it has meant since 0.3.53 - the geometrical
        sets - so a plan written against an older release does not silently start
        hiding sketches too. A string says which one.
        """
        if value is None or value is False:
            return ""
        if value is True:
            return "sets"
        mode = str(value).strip().lower()
        if mode in cls._CLEAN_MODES:
            return mode
        raise ArgumentRejected(
            f"clean={value!r} is not a mode. Use false (leave the picture alone), "
            "true (same as 'sets'), 'sets' (hide the geometrical sets), or "
            "'solid_only' (hide the sets AND the sketches inside the bodies - what "
            "a part built from sketch sections needs; measured on B30 2026-11-14, "
            "this is the only class that can be hidden without taking the solid "
            "off the screen).", code="BAD_CLEAN_MODE", stage="validate")

    def _screenshot(self, file_path: str, width: int = 0, height: int = 0,
                    clean: str = "", zoom: float = 1.0,
                    crop: Any = None) -> str:
        # A zoom that is not a usable number is a typo, and it is answerable
        # without a session (0.3.127's rule: refuse before doing anything).
        try:
            zoom = float(zoom if zoom is not None else 1.0)
        except (TypeError, ValueError):
            raise ArgumentRejected(
                f"zoom has to be a number (1.0 leaves the camera alone); got "
                f"{zoom!r}. Nothing was captured.", code="BAD_ARGUMENT",
                stage="validate")
        if not 0.05 <= zoom <= 20:
            raise ArgumentRejected(
                f"zoom={zoom:g} is outside 0.05..20. It multiplies the camera's "
                "current zoom, so 2 is twice as close and 0.5 twice as far; "
                "anything beyond that either loses the part or puts the camera "
                "inside it. Nothing was captured.", code="BAD_ARGUMENT",
                stage="validate")
        box = self._crop_box(crop)
        self.conn.ensure_connected()

        # Ensure output directory exists
        output_dir = os.path.dirname(file_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        # CatCaptureFormat: 2 = TIFF, 4 = BMP, 5 = JPEG (no PNG in CATIA V5)
        capture_formats = {".jpg": 5, ".jpeg": 5, ".bmp": 4, ".tif": 2, ".tiff": 2}
        ext = os.path.splitext(file_path)[1].lower()
        capture_format = capture_formats.get(ext)
        swapped_from = None
        if capture_format is None:
            # CATIA V5 has no PNG capture API (enum 2 = TIFF, 4 = BMP, 5 = JPEG),
            # so a .png request lands on JPEG. Say so instead of quietly writing
            # a file with a different extension than the caller asked for.
            swapped_from = ext or "(no extension)"
            file_path = os.path.splitext(file_path)[0] + ".jpg"
            capture_format = 5

        window = self.conn.active_window
        viewer = window.ActiveViewer
        # Ask the viewer to repaint first. It is one call and it cannot hurt;
        # measured on B30 it does not change the frame either (the "stale
        # screenshot" of the 2026-11-03 report was really the wrong view table,
        # fixed in _set_view), so nothing here claims to fix a timing problem.
        try:
            viewer.Update()
        except Exception:  # noqa: BLE001 - not every build exposes Update
            pass
        # THE SIZE IS CONTROLABLE, AND 0.3.103 SAID IT WAS NOT (2026-11-19 report,
        # §3.5, which asked for a conclusion rather than another version). That
        # note - "Window.Width/Height do not change it" - was measured on a
        # MAXIMIZED window, where the setter is a no-op; on a normal window the
        # capture follows the window exactly (B30 2026-11-19: 600/1100/1600 asked,
        # 600x877 / 1100x900 / 1600x900 written). So a requested size is honoured
        # by resizing the window, and the window is put back afterwards.
        resize_note, restore_size = self._apply_capture_size(window, width,
                                                             height, viewer)
        if restore_size is not None:
            # THE VIEWER IS BOUND TO THE WINDOW IT WAS TAKEN FROM. Measured on
            # B30 2026-11-19: after resizing, a capture through the OLD viewer
            # object wrote the previous frame at the previous size (808x744 for a
            # window that had just been set to 1200x800, and the restore then put
            # 772 back - so the resize looked like it had been ignored). Re-read
            # both, and repaint through the new one.
            try:
                window = self.conn.active_window
                viewer = window.ActiveViewer
                viewer.Update()
            except Exception:  # noqa: BLE001 - the resize note already said so
                pass
        hidden_note = ""
        records: list[Any] = []
        zoom_note = ""
        viewpoint = None
        base_zoom = None
        if zoom != 1.0:
            try:
                viewpoint = viewer.Viewpoint3D
                base_zoom = float(viewpoint.Zoom)
                viewpoint.Zoom = base_zoom * zoom
                # REPAINT AFTER THE ZOOM, not before it: the Update() above runs
                # while the camera is still where it was, and CaptureToFile does
                # not always force a redraw - measured on B30 2026-11-15, where
                # two captures at x1 and x1.6 came back byte-identical until this
                # line existed.
                try:
                    viewer.Update()
                except Exception:  # noqa: BLE001 - not every build exposes Update
                    pass
                got_zoom = float(viewpoint.Zoom)
                if abs(got_zoom - base_zoom * zoom) > 1e-6:
                    zoom_note = (f" [asked for zoom {base_zoom * zoom:.6f}, CATIA "
                                 f"kept {got_zoom:.6f}]")
                else:
                    zoom_note = (f" [zoom {base_zoom:.6f} -> {got_zoom:.6f}, "
                                 f"x{zoom:g}]")
            except Exception as error:  # noqa: BLE001 - the capture still happens
                zoom_note = (" [zoom could not be set ("
                             + str(error).splitlines()[0][:60] + ")]")
                viewpoint = None
        if clean:
            records, hidden_note = self._clean_view(clean)
            # REPAINT AFTER the hiding, exactly as the zoom path repaints after
            # the zoom. CaptureToFile does not always force a redraw (0.3.131
            # measured two byte-identical frames at x1 and x1.6 for this reason),
            # so a capture taken straight after a visibility change is a picture
            # of the previous frame - which is what made "hide the construction"
            # look ineffective on a part whose datum is a body member.
            try:
                viewer.Update()
            except Exception:  # noqa: BLE001 - not every build exposes Update
                pass
        try:
            # A RESIZED WINDOW IS NOT A RESIZED VIEW YET. Measured on B30
            # 2026-11-19: on a document created in the same automation call, a
            # capture taken 0.25 s after setting 1200x800 wrote 808x744 - the
            # view is laid out by CATIA's own message loop, which is busy running
            # this call. A one-second pause is enough (measured: 1.0 s -> 1200x800,
            # 0.25 s -> 808x744, and calling Reframe/Activate does NOT help). So
            # the size is VERIFIED on the file and the capture is retried, which
            # costs nothing when the first one is already right.
            expected = (f"{int(width or 0)}x{int(height or 0)}"
                        if (width or height) and int(width or 0) and int(height or 0)
                        else "")
            attempts = 3 if (restore_size is not None and expected) else 1
            for attempt in range(attempts):
                viewer.CaptureToFile(capture_format, os.path.normpath(file_path))
                if attempt + 1 >= attempts:
                    break
                if self._image_size(file_path) == expected:
                    break
                try:
                    viewer.Update()
                except Exception:  # noqa: BLE001 - not every build exposes Update
                    pass
                time.sleep(0.55)
        finally:
            if records:
                self._restore_view(records)
            if viewpoint is not None and base_zoom is not None:
                # PUT IT BACK: the caller's next capture is of the window they
                # were looking at, not of the magnification this call needed.
                try:
                    viewpoint.Zoom = base_zoom
                except Exception:  # noqa: BLE001 - a left zoom is not a failure
                    pass
            self._restore_window_size(window, restore_size)

        captured = self._image_size(file_path)
        note = ""
        if swapped_from:
            note += (f" [asked for '{swapped_from}', wrote JPEG: CATIA V5 can only "
                     "capture TIFF, BMP or JPEG]")
        asked = (int(width or 0), int(height or 0))
        if captured and asked != (0, 0) and captured != f"{asked[0]}x{asked[1]}":
            # The resize is the mechanism, so a file that still disagrees is
            # either a clamped window, a maximized one (said above) or a capture
            # taken before the repaint - worth naming, not worth hiding. Measured
            # BEFORE the crop: comparing a 300x200 crop against "asked for
            # 900x700" would be a complaint about the caller's own crop.
            note += (f" [asked for {asked[0]}x{asked[1]}, wrote {captured}: the "
                     "capture is the window's own size]")
        actual = captured
        crop_note = ""
        if box is not None:
            cropped = self._crop_image(file_path, box)
            if cropped is not None:
                crop_note = f" [cropped to {cropped} px]"
                actual = cropped
        size_note = f"{actual} px" if actual else "size unreadable"
        return (f"Screenshot saved to {file_path} ({size_note}){note}"
                f"{resize_note}{crop_note}{zoom_note}{hidden_note}"
                + (self._AXIS_TRIAD_NOTE if clean else ""))

    # ── the size and the crop, in one place ───────────────────────────────
    @staticmethod
    def _crop_box(crop: Any) -> tuple[int, int, int, int] | None:
        """Validate ``crop=[x, y, w, h]`` before anything is captured."""
        if crop is None:
            return None
        try:
            values = [int(value) for value in crop]
        except (TypeError, ValueError) as error:
            raise ArgumentRejected(
                f"crop has to be four integers [x, y, width, height] in pixels; "
                f"got {crop!r}. Nothing was captured.", code="BAD_ARGUMENT",
                stage="validate") from error
        if len(values) != 4:
            raise ArgumentRejected(
                f"crop needs four numbers [x, y, width, height]; got {len(values)}. "
                "Nothing was captured.", code="BAD_ARGUMENT", stage="validate")
        x, y, box_width, box_height = values
        if min(values) < 0 or box_width == 0 or box_height == 0:
            raise ArgumentRejected(
                f"crop={values} has a negative coordinate or a zero size. x/y are "
                "pixels from the top-left corner, width/height are pixels. Nothing "
                "was captured.", code="BAD_ARGUMENT", stage="validate")
        return x, y, box_width, box_height

    def _apply_capture_size(self, window: Any, width: Any, height: Any,
                            viewer: Any) -> tuple[str, tuple[int, int] | None]:
        """Resize the window to the requested capture size; return what to restore.

        Measured on B30 2026-11-19 (probe v0471): the capture is exactly the
        window's size, and ``Window.Width``/``Height`` are writable on a normal
        window. A MAXIMIZED window ignores them - that is what the 0.3.103 note
        measured, and it generalised one window state into "cannot be done".
        """
        wanted = [int(width or 0), int(height or 0)]
        if wanted == [0, 0]:
            return "", None
        try:
            state = int(getattr(window, "WindowState", 0))
        except Exception:  # noqa: BLE001 - unknown is not maximized
            state = 0
        if state != 0:
            return (" [the window is maximized, so it ignored the requested size - "
                    "restore it (or set WindowState=0) to choose the capture size]",
                    None)
        try:
            original = (int(window.Width), int(window.Height))
        except Exception as error:  # noqa: BLE001 - read-only build
            return (f" [the window size could not be read ({str(error).splitlines()[0][:60]}),"
                    " so the capture is whatever the window is]", None)
        try:
            if wanted[0]:
                window.Width = wanted[0]
            if wanted[1]:
                window.Height = wanted[1]
        except Exception as error:  # noqa: BLE001 - reported, not raised
            return (f" [the window would not resize ({str(error).splitlines()[0][:60]});"
                    f" captured at {original[0]}x{original[1]}]", None)
        try:
            viewer.Update()
        except Exception:  # noqa: BLE001 - not every build exposes Update
            pass
        # A capture straight after a resize has been seen to write the previous
        # frame; the sleep is 0.25 s and the file header is checked afterwards
        # either way, so a miss is reported rather than hidden.
        time.sleep(0.25)
        got = (int(getattr(window, "Width", 0)), int(getattr(window, "Height", 0)))
        expected = (wanted[0] or original[0], wanted[1] or original[1])
        note = ""
        if got != expected:
            # A size larger than the screen is clamped, and a window that is
            # mid-layout can refuse quietly; both are worth a sentence, because
            # the next line writes a file that does not match the request.
            note = (f" [asked for {expected[0]}x{expected[1]}, the window is "
                    f"{got[0]}x{got[1]}: a size larger than the screen is clamped, "
                    "and a window that is still being created can ignore the setter]")
        return note, original

    @staticmethod
    def _restore_window_size(window: Any, original: tuple[int, int] | None) -> None:
        if original is None:
            return
        try:
            window.Width, window.Height = original
        except Exception:  # noqa: BLE001 - a left size is not a failed capture
            pass

    @staticmethod
    def _crop_image(path: str, box: tuple[int, int, int, int]) -> str | None:
        """Cut ``box`` out of the file that was just written, in place.

        Pillow is optional (it is not in the runtime dependencies), so a machine
        without it gets a sentence, not a traceback - and never a silently
        uncropped picture.
        """
        try:
            from PIL import Image
        except ImportError as error:
            raise CodedError(
                "crop needs Pillow, which is not installed here, so the image was "
                "NOT cropped (it is the full window capture).",
                code="OPTIONAL_DEPENDENCY_MISSING", stage="validate",
                next_actions=["pip install pillow, then call catia_screenshot again"]) from error
        try:
            with Image.open(path) as image:
                width, height = image.size
                if box[0] + box[2] > width or box[1] + box[3] > height:
                    raise ArgumentRejected(
                        f"crop={list(box)} does not fit the {width}x{height} capture: "
                        f"x+width={box[0] + box[2]} and y+height={box[1] + box[3]}. "
                        "The image was written uncropped.", code="BAD_ARGUMENT",
                        stage="validate", modified=True)
                cut = image.crop((box[0], box[1], box[0] + box[2], box[1] + box[3]))
                cut.save(path)
                return f"{cut.size[0]}x{cut.size[1]}"
        except ArgumentRejected:
            raise
        except Exception as error:  # noqa: BLE001 - the capture itself is fine
            raise CodedError(
                f"The capture was written but could not be cropped "
                f"({str(error).splitlines()[0][:80]}).",
                code="CROP_FAILED", stage="execute", modified=True) from error

    # The 0.3.120 report photographed a clean part and found "a small white mesh
    # under the model" - the viewer's own axis system indicator, which it read as
    # a modelling defect. Said once, where the report image is taken: measured on
    # B30 2026-11-13, Viewer3D exposes 58 members and the only display switches
    # among them are lighting (LightSources/LightingMode/LightingIntensity) - no
    # axis, grid, triad or compass toggle at all.
    _AXIS_TRIAD_NOTE = (
        " The small axis triad at the bottom of the window belongs to the VIEWER, "
        "not to the part, and cannot be switched off from automation: Viewer3D "
        "exposes 58 members on B30 and the only display switches are lighting "
        "(measured 2026-11-13). Crop it out of a report image.")

    def _clean_view(self, mode: str = "sets") -> tuple[list[Any], str]:
        """Hide construction geometry for one capture; returns what to restore.

        The 2026-11-10 report followed this tool's own advice - hide the
        geometrical set before a screenshot - and got a blank picture, because
        the solid was a ThickSurface INSIDE that set. Hiding the set hides the
        model, and no reply said so.

        ``mode`` decides WHICH construction:

          "sets"       geometrical sets only - the original behaviour of
                       clean=true, right for a part built from GSD surfaces.
          "solid_only" the sets AND the sketches inside the solid bodies, which
                       is what a sketch-section part needs. The 2026-11-14 report
                       (P1-3) pointed a clean=true call at exactly that part and
                       got "this part has no geometrical sets, so nothing was
                       hidden" while 7 ellipse profiles sat on top of the solid.

        Neither mode touches a body.HybridShapes entry that CARRIES the solid, and
        that is a measurement, not a preference: that collection holds the solid
        itself on a Part Design loft (measured on B30 2026-11-14:
        body.HybridShapes = ['Datum_xy_20', 'Body']) and emptying it loses the
        window. Since 0.3.138 it is walked by NAME instead of skipped wholesale:
        every entry body.Shapes also lists is the solid and is left alone, and the
        rest - 'Datum_xy_30' and friends, one per section plane - is hidden. That
        is the leftover the 2026-11-15 report (C8) photographed after 28 sketches
        had already been reported hidden, and hiding it is safe: the solid's
        centre-light share was 0.591 before and after on B30 2026-11-15.
        """
        try:
            part = self.conn.get_active_part()
        except Exception:  # noqa: BLE001 - an assembly or no document
            return [], (" [clean was asked for but the active document has no part "
                        "to clean: no visibility was changed]")
        from catia_mcp.tools.measurement import (_hide_body_datums,
                                                _hide_body_sketches,
                                                _hide_construct_geometry)
        selection = getattr(self.conn, "hso", None)
        records, info = _hide_construct_geometry(part, selection)
        sketch_info: dict[str, Any] = {}
        if mode == "solid_only":
            sketch_records, sketch_info = _hide_body_sketches(part, selection)
            records = records + sketch_records
        # The body datums are not part of either previous category and are never
        # the solid, so both modes hide them.
        datum_records, datum_info = _hide_body_datums(part, selection)
        records = records + datum_records

        parts: list[str] = []
        if info.get("sets"):
            parts.append(f"{info.get('hidden', 0)} construction element(s) in "
                         f"{info.get('sets', 0)} set(s)")
        if sketch_info.get("sketch_hidden"):
            parts.append(f"{sketch_info['sketch_hidden']} sketch(es) inside "
                         f"{sketch_info.get('bodies', 0)} body/bodies")
        if datum_info.get("hidden"):
            parts.append(f"{datum_info['hidden']} datum(s) inside "
                         f"{datum_info.get('bodies', 0) or 1} body/bodies")
        if not records:
            return [], (" [clean was asked for but there was nothing this mode can "
                        "hide without taking the solid with it: this part has no "
                        "geometrical sets"
                        + (" and no sketches inside its bodies" if mode == "solid_only"
                           else "")
                        + ", so nothing was hidden. That is not a failure - the "
                        "picture is as clean as it gets without a support the solid "
                        "draws from, measured on B30 2026-11-14]")
        kept = info.get("kept") or []
        return records, (
            f" [clean ({mode}): " + " and ".join(parts) + " hidden for the capture"
            + (f", keeping {', '.join(repr(k) for k in kept[:3])} visible because "
               f"it carries the solid" if kept else "")
            + "; all flags restored afterwards. NOT HIDDEN, on purpose: the three "
              "absolute datum planes, which this build does not draw anyway "
              "(measured 2026-11-15: hiding them leaves the capture byte-identical "
              "on a pad part AND on a loft part, so there is nothing there to "
              "clean). The specification tree is still in the picture - no "
              "automation member hides it (measured 2026-11-10)]")

    def _restore_view(self, records: list[Any]) -> None:
        """Put every visibility flag back after a capture."""
        from catia_mcp.tools.measurement import _restore_visibility

        _restore_visibility(records, getattr(self.conn, "hso", None))
        try:
            self.conn.refresh_display()
        except Exception:  # noqa: BLE001 - the file is already written
            pass

    def _set_view(self, view: str | None, direction: list[float] | None = None,
                  up: list[float] | None = None,
                  projection: str = "parallel") -> str:
        self.conn.ensure_connected()
        viewer = self.conn.active_window.ActiveViewer
        viewpoint = viewer.Viewpoint3D

        # CATIA convention: X right, Y depth, Z up. `SightDirection` is the way
        # the CAMERA LOOKS (so "front" looks along +Y, from the -Y side, with Z
        # up), and `UpDirection` is the screen's up vector.
        #
        # The table through 0.3.49 had front/back and top/bottom SWAPPED, and an
        # up vector of Y on the side views: set_view('front') produced a top-down
        # picture (looking along -Z), set_view('top') an upside-down front
        # elevation, and set_view('bottom') something that looked like a front
        # view. That is exactly what the 2026-11-03 acceptance report described,
        # and it is why two screenshots taken "before and after" a view change
        # came back identical - both were the same wrong view, not a timing
        # problem. Verified on B30 with a 40x10x4 plate carrying a tower on its
        # +Z face at +X: one screenshot per view, each distinguishable.
        views = {
            "front":     {"sight": (0, 1, 0),   "up": (0, 0, 1)},
            "back":      {"sight": (0, -1, 0),  "up": (0, 0, 1)},
            "top":       {"sight": (0, 0, -1),  "up": (0, 1, 0)},
            "bottom":    {"sight": (0, 0, 1),   "up": (0, -1, 0)},
            "left":      {"sight": (1, 0, 0),   "up": (0, 0, 1)},
            "right":     {"sight": (-1, 0, 0),  "up": (0, 0, 1)},
            "isometric": {"sight": (-1, -1, -1), "up": (-1, -1, 2)},
        }

        if projection not in ("parallel", "perspective"):
            raise ArgumentRejected(
                f"projection must be 'parallel' or 'perspective'; got "
                f"{projection!r}. Nothing was changed.",
                code="BAD_ARGUMENT", stage="validate")

        if direction is not None:
            sight = self._three(direction, "direction")
            if up is None:
                # An up vector that is not parallel to the sight: Z for anything
                # nearly horizontal, Y for a near-vertical look.
                up_vec = (0.0, 1.0, 0.0) if abs(sight[2]) > 0.9 else (0.0, 0.0, 1.0)
                up_note = (f" (up defaulted to {up_vec} because none was given "
                           "- it only has to be non-parallel to 'direction')")
            else:
                up_vec = self._three(up, "up")
                up_note = ""
            label = "custom direction"
            if view:
                label = f"custom direction (view='{view}' ignored)"
        else:
            if not view:
                raise ArgumentRejected(
                    "catia_set_view needs 'view' (front, back, top, bottom, left, "
                    "right, isometric) or 'direction' [x, y, z] plus optional "
                    "'up'. Nothing was changed.",
                    code="VIEW_REQUIRED", stage="validate")
            if view not in views:
                raise ArgumentRejected(
                    f"Unknown view: '{view}'",
                    code="UNKNOWN_VIEW", stage="validate",
                    data={"known": sorted(views)})
            v = views[view]
            sight = tuple(float(c) for c in v["sight"])
            up_vec = tuple(float(c) for c in v["up"])
            up_note = ""
            label = view

        cross = (sight[1] * up_vec[2] - sight[2] * up_vec[1],
                 sight[2] * up_vec[0] - sight[0] * up_vec[2],
                 sight[0] * up_vec[1] - sight[1] * up_vec[0])
        if sum(abs(c) for c in cross) < 1e-9:
            raise ArgumentRejected(
                f"'up' {list(up_vec)} is parallel to the sight direction "
                f"{list(sight)}, which leaves the picture's rotation undefined. "
                "Pick an up vector across the sight. Nothing was changed.",
                code="UP_PARALLEL_TO_SIGHT", stage="validate")

        # ── the projection, which is what the tilt actually was ────────────
        # BOTH 0.3.120 acceptance reports said set_view('front') is "15-20 degrees
        # off with the bottom of the bottle visible", and both blamed the sight
        # vector. It is not: measured on B30 2026-11-13, GetSightDirection reads
        # back exactly (0,1,0) and GetUpDirection (0,0,1) right after the Put, so
        # the camera IS on -Y. What was left is ProjectionMode, which reads 0 on
        # this install - PERSPECTIVE - and a perspective camera that has to frame
        # a 60x40x4 plate looks down on it slightly, because the eye point sits
        # above the plate's plane. Set to 1 (PARALLEL) the same plate is a clean
        # elevation: measured with two captures that differ in nothing else
        # (scripts/b30_v0423_probe_view2.py). ProjectionMode=2 is not a third
        # mode on B30 - it is silently kept at 1.
        want = 1 if projection == "parallel" else 0
        try:
            viewpoint.ProjectionMode = want
            got_mode = int(viewpoint.ProjectionMode)
            mode_note = ""
            if got_mode != want:
                mode_note = (f" CATIA kept ProjectionMode={got_mode} instead of "
                             f"{want} - only 0 (perspective) and 1 (parallel) "
                             "exist on B30.")
        except Exception as error:  # noqa: BLE001 - reported, the view still sets
            got_mode = None
            mode_note = (" The projection mode could not be set ("
                         + str(error).splitlines()[0][:70] + ").")

        viewpoint.PutSightDirection(list(sight))
        viewpoint.PutUpDirection(list(up_vec))
        viewer.Reframe()

        actual = self._view_directions(viewpoint)
        if actual is None:
            read_back = (" The direction could not be read back (Viewpoint3D "
                         "exposes GetSightDirection, and the by-ref array does not "
                         "marshal late-bound), so this reply does NOT confirm the "
                         "camera took them.")
        else:
            got_sight, got_up = actual
            # COMPARE ANGLES, NOT COMPONENTS. 0.3.121 compared the read-back with
            # the REQUESTED numbers component by component at 1e-6, and CATIA
            # normalises what it stores - so every non-unit direction and every
            # named diagonal view was reported as "the Put was ignored". The
            # 0.3.122 report (P0-2) measured it both ways: isometric's
            # (-1,-1,-1)/(-1,-1,2) reads back as (-0.5774,-0.5774,-0.5774) and
            # (-0.4082,-0.4082,0.8165), which IS the same view (1/sqrt3, 2/sqrt6),
            # and sending those normalised values in made the same call "pass" -
            # same camera, different wording. A direction has no length, so the
            # test has to be an angle: sight within 0.5 deg, up within 1 deg after
            # the same Gram-Schmidt CATIA applies.
            sight_angle = self._angle_between(sight, got_sight)
            up_angle = self._angle_between(self._orthonormal_up(sight, up_vec),
                                           got_up)
            taken = (sight_angle is not None and up_angle is not None
                     and sight_angle <= 0.5 and up_angle <= 1.0)
            shown_sight = [round(c, 4) for c in self._unit(sight)]
            shown_up = [round(c, 4) for c in self._unit(got_up)]
            if taken:
                read_back = (
                    f" Read back from CATIA: sight {got_sight}, up {got_up} - the "
                    f"camera took them (sight is {sight_angle:.2f} deg and up "
                    f"{up_angle:.2f} deg from what was asked; CATIA stores both as "
                    "unit vectors and squares the up vector to the sight, so those "
                    f"numbers differ from the request by exactly that: {shown_sight} "
                    f"and {shown_up} is the same view).")
            else:
                read_back = (
                    f" READ BACK FROM CATIA: sight {got_sight}, up {got_up}, which "
                    "is NOT the direction asked for - sight differs by "
                    + (f"{sight_angle:.2f} deg" if sight_angle is not None
                       else "an unmeasurable amount")
                    + (f" and up by {up_angle:.2f} deg" if up_angle is not None
                       else "")
                    + ". The requested direction is "
                    f"{shown_sight} (up, squared to it: "
                    f"{[round(c, 4) for c in self._orthonormal_up(sight, up_vec)]}), "
                    "so this is not a normalisation difference - the picture is not "
                    "this view.")
        mode_text = ("parallel projection" if got_mode == 1
                     else "perspective projection" if got_mode == 0
                     else "projection unknown")
        return (f"View set to {label}: {mode_text}, sight {list(sight)}, up "
                f"{list(up_vec)}.{up_note}{mode_note}{read_back}")

    @staticmethod
    def _unit(vector: Any) -> list[float]:
        values = [float(v) for v in vector]
        length = math.sqrt(sum(v * v for v in values))
        if length < 1e-12:
            return [0.0, 0.0, 0.0]
        return [v / length for v in values]

    @classmethod
    def _orthonormal_up(cls, sight: Any, up: Any) -> list[float]:
        """``up`` with its component along ``sight`` removed, then normalised.

        This is what CATIA does to a view's up vector before storing it - the
        0.3.122 report sent up=[0,0,1] with sight=[0.45,1,-0.18] and read back
        [0.0665,0.1477,0.9868], which is exactly that projection.
        """
        s_unit = cls._unit(sight)
        u = [float(v) for v in up]
        dot = sum(a * b for a, b in zip(u, s_unit))
        remainder = [v - dot * c for v, c in zip(u, s_unit)]
        return cls._unit(remainder)

    @classmethod
    def _angle_between(cls, one: Any, other: Any) -> float | None:
        """Angle in degrees between two directions, or None if either is zero."""
        a, b = cls._unit(one), cls._unit(other)
        if not any(abs(v) > 0 for v in a) or not any(abs(v) > 0 for v in b):
            return None
        dot = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))
        return math.degrees(math.acos(dot))

    def _three(self, value: Any, what: str) -> tuple[float, float, float]:
        """Three finite numbers from a caller, or a refusal that names the fault."""
        try:
            numbers = [float(v) for v in value]
        except (TypeError, ValueError):
            raise ArgumentRejected(
                f"'{what}' must be three numbers [x, y, z]; got {value!r}. "
                "Nothing was changed.", code="BAD_ARGUMENT", stage="validate")
        if len(numbers) != 3:
            raise ArgumentRejected(
                f"'{what}' must have exactly three components; got {len(numbers)}. "
                "Nothing was changed.", code="BAD_ARGUMENT", stage="validate")
        if not any(abs(n) > 0 for n in numbers):
            raise ArgumentRejected(
                f"'{what}' is [0, 0, 0], which has no direction. Nothing was "
                "changed.", code="BAD_ARGUMENT", stage="validate")
        return (numbers[0], numbers[1], numbers[2])

    _VIEW_DIRECTIONS_VBS = (
        "Public Function catia_mcp_viewdirs(oVP)\n"
        "    Dim s(2), u(2), a(5)\n"
        "    oVP.GetSightDirection s\n"
        "    oVP.GetUpDirection u\n"
        "    a(0)=s(0) : a(1)=s(1) : a(2)=s(2)\n"
        "    a(3)=u(0) : a(4)=u(1) : a(5)=u(2)\n"
        "    catia_mcp_viewdirs = a\n"
        "End Function\n"
    )

    def _view_directions(self, viewpoint: Any) -> tuple[list[float], list[float]] | None:
        """The view's actual sight/up, read through VBS; None if unreadable.

        ``Viewpoint3D.SightDirection`` is not a property on B30: the typelib has
        ``GetSightDirection`` with a by-ref array, which does not marshal
        late-bound (measured 2026-11-13), so the same SystemService.Evaluate route
        the Measurable arrays use is needed.
        """
        try:
            raw = self.conn.app.SystemService.Evaluate(
                self._VIEW_DIRECTIONS_VBS, 0, "catia_mcp_viewdirs", [viewpoint])
            values = [round(float(v), 4) for v in raw]
            return values[:3], values[3:6]
        except Exception:  # noqa: BLE001 - the reply says so
            return None

    def _fit_all(self) -> str:
        self.conn.ensure_connected()
        viewer = self.conn.active_window.ActiveViewer
        viewer.Reframe()
        return "View fitted to all geometry"
