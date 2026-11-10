"""Export tools for CATIA V5.

Export to STEP, IGES, STL, 3DXML, and other formats.
Also includes screenshot capture.
"""

from __future__ import annotations

import json
import os
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
                    "is saved as .jpg instead). THE SIZE IS THE 3D WINDOW'S, NOT YOURS: "
                    "CATIA V5 captures the viewer's client area and exposes no "
                    "capture-resolution setting (measured on B30 2026-11-03, where "
                    "900x900 was asked for and 300x400 written). The reply reports the "
                    "real pixel size read from the file header, and says so when it "
                    "differs from the request - resize the CATIA window if the picture "
                    "has to be bigger. "
                    "THE PICTURE IS NOT A CLEAN RENDER: it is the window's client "
                    "area, so the specification tree and the axis are in it (reported "
                    "2026-11-08, P2-1). There is no supported way to suppress it from "
                    "automation - `Application.StartCommand` does NOT validate the "
                    "command name (an unknown one is accepted and then opens a modal "
                    "未知命令 dialog on the automation thread, measured 2026-11-08), so "
                    "a guessed command is not a shortcut, it is a freeze. Hide the "
                    "tree in the UI (F3) before calling this if the picture is for a "
                    "report."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Output image path (e.g., 'C:/screenshots/part.png')",
                        },
                        "width": {
                            "type": "integer",
                            "description": (
                                "Requested image width in pixels - ADVISORY: CATIA "
                                "captures the 3D window's client area and cannot be "
                                "told another size. The reply reports what was written."
                            ),
                            "default": 1920,
                        },
                        "height": {
                            "type": "integer",
                            "description": (
                                "Requested image height in pixels - ADVISORY, see "
                                "'width'; the reply reports the real size."
                            ),
                            "default": 1080,
                        },
                        "clean": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Hide the geometrical sets, capture, and put every "
                                "visibility flag back. This is what makes a picture "
                                "of the SOLID: with construction surfaces on screen "
                                "they draw over it, and if the solid itself lives in "
                                "one of those sets (a ThickSurface does) hiding the "
                                "set by hand gives a blank window - reported "
                                "2026-11-10. The elements carrying the solid are kept "
                                "visible, so the model is still in the picture, and "
                                "the reply says how many flags were restored. "
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
                    "transform) will not line up with any of them, and no "
                    "automation call reports the current right/up vectors the way "
                    "the UI's 'Normal to' does. Build in the axes you want to "
                    "photograph, or take the screenshot before placing the "
                    "component (reported 0.3.50 as P3, documented here since "
                    "0.3.59)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "view": {
                            "type": "string",
                            "description": "View orientation",
                            "enum": [
                                "front", "back", "top", "bottom",
                                "left", "right", "isometric",
                            ],
                        },
                    },
                    "required": ["view"],
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
                    arguments.get("width", 1920),
                    arguments.get("height", 1080),
                    bool(arguments.get("clean", False)),
                )
            case "catia_set_view":
                return self._set_view(arguments["view"])
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

        return f"Exported to {file_path}{size_info} (format: {fmt_key.upper()})"

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

    def _screenshot(self, file_path: str, width: int = 1920, height: int = 1080,
                    clean: bool = False) -> str:
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

        viewer = self.conn.active_window.ActiveViewer
        # Ask the viewer to repaint first. It is one call and it cannot hurt;
        # measured on B30 it does not change the frame either (the "stale
        # screenshot" of the 2026-11-03 report was really the wrong view table,
        # fixed in _set_view), so nothing here claims to fix a timing problem.
        try:
            viewer.Update()
        except Exception:  # noqa: BLE001 - not every build exposes Update
            pass
        hidden_note = ""
        records: list[Any] = []
        if clean:
            records, hidden_note = self._clean_view()
        try:
            viewer.CaptureToFile(capture_format, os.path.normpath(file_path))
        finally:
            if records:
                self._restore_view(records)

        actual = self._image_size(file_path)
        note = ""
        if swapped_from:
            note += (f" [asked for '{swapped_from}', wrote JPEG: CATIA V5 can only "
                     "capture TIFF, BMP or JPEG]")
        size_note = f"{actual} px" if actual else "size unreadable"
        if actual and (int(width or 0), int(height or 0)) != (0, 0):
            requested = f"{int(width)}x{int(height)}"
            if requested != actual:
                note += (f" [asked for {requested}; CATIA V5 captures the 3D window's "
                         "client area and exposes no capture-resolution setting - "
                         "Window.Width/Height do not change it, measured on B30 "
                         "2026-11-03. Resize the CATIA window to change this.]")
        return f"Screenshot saved to {file_path} ({size_note}){note}{hidden_note}"

    def _clean_view(self) -> tuple[list[Any], str]:
        """Hide construction geometry for one capture; returns what to restore.

        The 2026-11-10 report followed this tool's own advice - hide the
        geometrical set before a screenshot - and got a blank picture, because
        the solid was a ThickSurface INSIDE that set. Hiding the set hides the
        model, and no reply said so.
        """
        try:
            part = self.conn.get_active_part()
        except Exception:  # noqa: BLE001 - an assembly or no document
            return [], (" [clean was asked for but the active document has no part "
                        "to clean: no visibility was changed]")
        from catia_mcp.tools.measurement import _hide_construct_geometry
        records, info = _hide_construct_geometry(part, getattr(self.conn, "hso",
                                                               None))
        if not records:
            return [], (" [clean was asked for but this part has no geometrical "
                        "sets, so nothing was hidden]")
        kept = info.get("kept") or []
        return records, (
            f" [clean: {info.get('hidden', 0)} construction element(s) in "
            f"{info.get('sets', 0)} set(s) hidden for the capture"
            + (f", keeping {', '.join(repr(k) for k in kept[:3])} visible because "
               f"it carries the solid" if kept else "")
            + "; all flags restored afterwards. The specification tree is still in "
              "the picture - no automation member hides it (measured 2026-11-10)]")

    def _restore_view(self, records: list[Any]) -> None:
        """Put every visibility flag back after a capture."""
        from catia_mcp.tools.measurement import _restore_visibility

        _restore_visibility(records, getattr(self.conn, "hso", None))
        try:
            self.conn.refresh_display()
        except Exception:  # noqa: BLE001 - the file is already written
            pass

    def _set_view(self, view: str) -> str:
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

        if view not in views:
            raise ArgumentRejected(f"Unknown view: '{view}'")

        v = views[view]
        sight = v["sight"]
        up = v["up"]

        # Set viewpoint sight and up directions
        viewpoint.PutSightDirection(list(sight))
        viewpoint.PutUpDirection(list(up))

        # Fit all in view
        viewer.Reframe()

        return f"View set to: {view}"

    def _fit_all(self) -> str:
        self.conn.ensure_connected()
        viewer = self.conn.active_window.ActiveViewer
        viewer.Reframe()
        return "View fitted to all geometry"
