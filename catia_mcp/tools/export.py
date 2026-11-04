"""Export tools for CATIA V5.

Export to STEP, IGES, STL, 3DXML, and other formats.
Also includes screenshot capture.
"""

from __future__ import annotations

import json
import os
from typing import Any

from catia_mcp.connection import CATIAConnection

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
                    "has to be bigger."
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
                )
            case "catia_set_view":
                return self._set_view(arguments["view"])
            case "catia_fit_all":
                return self._fit_all()
            case _:
                raise ValueError(f"Unknown export tool: {tool_name}")

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
            raise ValueError(
                f"Unsupported export format: '{fmt}'. Supported: {supported}"
            )

        # Ensure output directory exists
        output_dir = os.path.dirname(file_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        # CATIA V5 export via SaveAs with format specification.
        # Normalize separators: ExportData rejects forward-slash paths (E_FAIL).
        doc.ExportData(os.path.normpath(file_path), FORMAT_MAP[fmt_key])

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

    def _screenshot(self, file_path: str, width: int = 1920, height: int = 1080) -> str:
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
        viewer.CaptureToFile(capture_format, os.path.normpath(file_path))

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
        return f"Screenshot saved to {file_path} ({size_note}){note}"

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
            raise ValueError(f"Unknown view: '{view}'")

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
