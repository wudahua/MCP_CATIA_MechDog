"""CATIA V5 COM Connection Manager.

Manages the connection to CATIA V5 via Windows COM Automation API (win32com).
Supports connecting to a running instance or launching a new one.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("catia_mcp")

# COM imports are deferred to runtime (Windows only)
try:
    import pythoncom
    import win32com.client

    HAS_COM = True
except ImportError:
    HAS_COM = False


class CATIAConnection:
    """Manages connection to CATIA V5 via COM Automation."""

    # CATIA V5 COM ProgID
    CATIA_PROGID = "CATIA.Application"

    def __init__(self) -> None:
        self.app: Any | None = None
        self._initialized_com = False
        # Last sketch this session created and closed. body.Sketches is ordered
        # by tree position, not creation order, so "the last sketch" cannot be
        # read back from the collection - see PartDesignTools._get_last_sketch.
        self.last_sketch: Any | None = None
        # Solid body new features should go into. None = the part's MainBody.
        self.active_body: Any | None = None
        # What this server drew inside each sketch, plus where the sketch sits:
        #   {sketch name: {"placement": {"base": "xy", "distance": 50.0} | None,
        #                  "elements": [{"kind": "line", "x1": .., ...}, ...]}}
        # The only way to get an outline's coordinates: a CATIA Point2D exposes
        # no readable X/Y through automation on this build, so the numbers have
        # to be remembered at drawing time. catia_gsd_spline(element=...) reads
        # this; a sketch drawn elsewhere (or a reopened file) has no record.
        self.sketch_outlines: dict[str, dict[str, Any]] = {}
        # Old sketch name -> the name of the sketch re-created in its place, per
        # document: {"<document>::<name>": "<new name>"}. A rollback sometimes
        # takes the sketch that built the feature with it, and the sketch this
        # server redraws from its record gets the next free name. Remembering the
        # mapping is what lets the caller retry with the name it already used
        # instead of getting SKETCH_NOT_FOUND for its own sketch.
        self.sketch_aliases: dict[str, str] = {}

    @property
    def is_connected(self) -> bool:
        """Check if we have an active CATIA connection."""
        if self.app is None:
            return False
        try:
            # Try accessing a property to verify the connection is alive
            _ = self.app.Caption
            return True
        except Exception:
            self.app = None
            return False

    def connect(self) -> str:
        """Connect to CATIA V5. Tries running instance first, then launches new one.

        Returns a status message string.
        """
        if not HAS_COM:
            raise RuntimeError(
                "pywin32 is not installed. Install it with: pip install pywin32\n"
                "Note: This MCP server requires Windows with CATIA V5 installed."
            )

        if self.is_connected:
            version = self._get_version()
            return f"Already connected to CATIA V5 ({version})"

        # Initialize COM for this thread
        if not self._initialized_com:
            pythoncom.CoInitialize()
            self._initialized_com = True

        # Phase 1: Try to attach to a running CATIA instance
        try:
            self.app = win32com.client.GetActiveObject(self.CATIA_PROGID)
            version = self._get_version()
            logger.info("Connected to running CATIA V5 instance (%s)", version)
            return f"Connected to running CATIA V5 instance ({version})"
        except Exception:
            logger.info("No running CATIA instance found, launching new one...")

        # Phase 2: Launch a new CATIA instance
        try:
            self.app = win32com.client.Dispatch(self.CATIA_PROGID)
            self.app.Visible = True
            version = self._get_version()
            logger.info("Launched new CATIA V5 instance (%s)", version)
            return f"Launched new CATIA V5 instance ({version})"
        except Exception as e:
            self.app = None
            raise RuntimeError(
                f"Failed to connect to CATIA V5: {e}\n"
                "Make sure CATIA V5 is installed and licensed on this machine."
            ) from e

    def disconnect(self) -> str:
        """Disconnect from CATIA V5 (does not close CATIA)."""
        if self.app is not None:
            self.app = None
        if self._initialized_com:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
            self._initialized_com = False
        return "Disconnected from CATIA V5"

    def ensure_connected(self) -> None:
        """Ensure we have an active CATIA connection, connecting if needed."""
        if not self.is_connected:
            self.connect()

    def _get_version(self) -> str:
        """Get CATIA version string."""
        try:
            # CATIA V5 exposes SystemService.Environ or Caption
            caption = self.app.Caption
            return caption if caption else "unknown version"
        except Exception:
            return "unknown version"

    # ── Helper properties for quick access to CATIA objects ──

    @property
    def documents(self) -> Any:
        """Get the CATIA Documents collection."""
        self.ensure_connected()
        return self.app.Documents

    @property
    def active_document(self) -> Any:
        """Get the active CATIA document."""
        self.ensure_connected()
        try:
            return self.app.ActiveDocument
        except Exception:
            raise RuntimeError("No active document in CATIA. Create or open a document first.")

    @property
    def active_window(self) -> Any:
        """Get the active window (CATIA V5 has no ActiveEditor)."""
        self.ensure_connected()
        return self.app.ActiveWindow

    @property
    def hso(self) -> Any:
        """Get the Highlighted Set of Objects (selection)."""
        self.ensure_connected()
        return self.active_document.Selection

    def refresh_display(self) -> None:
        """Refresh the CATIA 3D view."""
        try:
            self.active_window.ActiveViewer.Reframe()
        except Exception:
            pass

    # ── Document type detection ──

    @staticmethod
    def _dynamic(obj: Any) -> Any:
        """Re-wrap a COM object in a purely dynamic dispatcher.

        ``win32com.client.GetActiveObject`` resolves through the pywin32 gen_py
        cache, and a cached ``Document`` exposes only the base interface: reading
        ``doc.Part`` then fails with ``AttributeError: <unknown>.Part`` even
        though the live object implements it. Querying the same object
        dynamically hits the real IDispatch, where ``Part``/``Product`` resolve.
        Behaviour therefore depends on cache state, which is why this is done
        defensively rather than assumed.
        """
        try:
            return win32com.client.dynamic.Dispatch(obj._oleobj_)
        except Exception:
            return obj

    def get_active_part(self) -> Any:
        """Get the Part object from the active PartDocument."""
        return self.part_of(self.active_document)

    def get_active_product(self) -> Any:
        """Get the Product object from the active ProductDocument."""
        return self.product_of(self.active_document)

    def _interface(self, doc: Any, name: str) -> Any:
        for candidate in (doc, self._dynamic(doc)):
            try:
                return getattr(candidate, name)
            except Exception:
                continue
        raise RuntimeError(
            f"Active document '{getattr(doc, 'Name', '?')}' exposes no '{name}'. "
            + (
                "Open or create a Part document first."
                if name == "Part"
                else "Open or create an Assembly (Product) document first."
            )
        )

    def part_of(self, doc: Any) -> Any:
        """The Part behind a PartDocument, robust to the gen_py cache state."""
        return self._interface(doc, "Part")

    def product_of(self, doc: Any) -> Any:
        """The Product behind a ProductDocument, robust to the gen_py cache state."""
        return self._interface(doc, "Product")

    def get_active_part_body(self) -> Any:
        """Get the solid body new features should go into.

        Defaults to MainBody, but catia_new_body switches it: a part can hold
        several solid bodies, and each pad/pocket/hole belongs to exactly one.
        The handle is remembered on the connection because part.Bodies is a
        collection with no "current" notion of its own.
        """
        part = self.get_active_part()
        remembered = getattr(self, "active_body", None)
        if remembered is not None:
            try:
                _ = remembered.Name          # still alive?
                return remembered
            except Exception:
                self.active_body = None
        return part.MainBody

    def get_origin_elements(self) -> dict[str, Any]:
        """Get the origin planes (XY, YZ, ZX) from the active Part."""
        part = self.get_active_part()
        origin = part.OriginElements
        return {
            "xy": origin.PlaneXY,
            "yz": origin.PlaneYZ,
            "zx": origin.PlaneZX,
        }


# ── session state ────────────────────────────────────────────────────────
#
# CATIA answers most automation failures with a bare "method failed", and the
# cause is almost never the arguments - it is the document state: a factory
# called while InWorkObject sits on a geometrical set, a topology reference that
# a cleared selection invalidated, a body whose last feature has not rebuilt.
# Both the failure message and `catia_diagnose` need that state, and they used to
# read it differently: the message printed five readings while `catia_diagnose`
# returned none of them, so the message's "catia_diagnose reports both" pointed
# at an empty place (reported 2026-10-27). One reader, two presentations.


def session_snapshot(conn: Any) -> dict[str, Any]:
    """The document state that explains a bare "method failed", as fields.

    Never raises: a diagnosis that throws is worse than one that says "unknown".
    A field that cannot be read is ``None`` with the reason in ``note`` - the
    five readings only mean something together, and half of them are only
    meaningful when a Part is active.
    """
    snapshot: dict[str, Any] = {
        "document": None,
        "document_kind": None,
        "part": None,
        "in_work_object": None,
        "body": None,
        "shape_count": None,
        "selected": None,
        "geometrical_sets": None,
        "note": None,
    }
    try:
        doc = conn.active_document
        snapshot["document"] = str(doc.Name)
    except Exception:  # noqa: BLE001 - reported as None
        snapshot["note"] = ("no active document in CATIA, so there is no state to "
                            "read - open or create one first")
        return snapshot
    try:
        kind = str(getattr(doc, "Type", "") or "")
    except Exception:  # noqa: BLE001
        kind = ""
    if kind:
        snapshot["document_kind"] = ("assembly" if "PRODUCT" in kind.upper()
                                     else "part" if "PART" in kind.upper()
                                     else kind)
    try:
        snapshot["selected"] = int(conn.hso.Count)
    except Exception:  # noqa: BLE001 - the selection may be unusable
        pass
    try:
        part = conn.get_active_part()
    except Exception:  # noqa: BLE001 - not a Part document
        if snapshot["document_kind"] is None:
            # `Document.Type` is empty or unreadable on some bindings, so fall
            # back to the question the tools themselves ask.
            try:
                conn.product_of(doc)
                snapshot["document_kind"] = "assembly"
            except Exception:  # noqa: BLE001
                pass
        snapshot["note"] = ("document is open but has no Part, so in_work_object / "
                            "body / geometrical_sets have nothing to report (an "
                            "assembly measures per component instead)")
        return snapshot
    for key, reader in (
        ("part", lambda: str(part.Name)),
        ("in_work_object", lambda: str(part.InWorkObject.Name)),
        ("body", lambda: str(conn.get_active_part_body().Name)),
        ("shape_count", lambda: int(conn.get_active_part_body().Shapes.Count)),
        ("geometrical_sets", lambda: int(part.HybridBodies.Count)),
    ):
        try:
            snapshot[key] = reader()
        except Exception:  # noqa: BLE001 - a half-read state still helps
            pass
    if snapshot["document_kind"] is None:
        # Reaching `part` proves it IS a part, whatever `Type` said.
        snapshot["document_kind"] = "part"
    return snapshot


def session_line(conn: Any) -> str:
    """The same snapshot as one line: the form the failure message carries.

    Kept identical in shape to what it replaced, because a report quotes it
    verbatim and the fields are read left to right in a terminal.
    """
    snap = session_snapshot(conn)
    bits = ["document=%s" % (snap["document"] or "<none>")]
    if snap["part"] is None:
        bits.append("no part document")
        if snap["selected"] is not None:
            bits.append("selected=%d" % snap["selected"])
        return " | ".join(bits)
    bits.append("in_work_object=%s" % (snap["in_work_object"] or "<unknown>"))
    if snap["body"] is None:
        bits.append("body=<none>")
    else:
        bits.append("body=%s(shape_count=%s)" % (
            snap["body"], snap["shape_count"]
            if snap["shape_count"] is not None else "?"))
    if snap["selected"] is not None:
        bits.append("selected=%d" % snap["selected"])
    if snap["geometrical_sets"] is not None:
        bits.append("geometrical_sets=%d" % snap["geometrical_sets"])
    return " | ".join(bits)
