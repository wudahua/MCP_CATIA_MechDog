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
        #
        # ONE definition of "what does this name mean" lives here (0.3.32): every
        # tool that takes a sketch name resolves it through ``sketch_alias``, and
        # every tool that lists sketches shows the aliases. Until then only Part
        # Design resolved them, so ``catia_pad`` knew 草图.2 was 草图.3 and
        # ``catia_sketch_get_geometry`` answered "No sketch named '草图.2'" for
        # the same live sketch (reported 2026-10-27).
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

    # ── Sketch names: one definition, used by every tool ──

    def sketch_scope(self) -> str:
        """The document key the sketch records and aliases are scoped by."""
        try:
            return str(self.active_document.Name)
        except Exception:  # noqa: BLE001 - record it anyway, under a catch-all
            return "?"

    def note_sketch_alias(self, lost: str, new: str,
                          document: str | None = None) -> None:
        """Record that ``lost`` was re-created as ``new`` - and re-point the rest.

        The first cut (0.3.31) wrote one hop: ``草图.2 -> 草图.3``. A second
        rollback then deleted 草图.3, rebuilt it as 草图.4 and wrote ``草图.3 ->
        草图.4``, so the name the caller actually held stopped resolving on the
        third retry - while the sketch it wanted was in the tree the whole time
        (reported 2026-10-27: retry 1 and 2 answered NO_MATERIAL, retry 3 answered
        SKETCH_NOT_FOUND). Re-pointing every entry that pointed at ``lost`` keeps
        the caller's original name valid for the rest of the session, and keeps
        the table one level deep so a lookup never has to walk.
        """
        scope = document or self.sketch_scope()
        aliases = self.sketch_aliases
        aliases[f"{scope}::{lost}"] = new
        for key, target in list(aliases.items()):
            if key != f"{scope}::{lost}" and target == lost:
                aliases[key] = new

    def sketch_alias(self, name: str, document: str | None = None) -> str | None:
        """What ``name`` was re-created as, or None. Follows a chain if one exists.

        ``note_sketch_alias`` keeps the table one level deep, so the loop is
        normally a single step; it is here because a chain can still appear (a
        mapping written by an older build, or a rebuild whose re-pointing lost a
        race), and a lookup that silently gives up on a chain would recreate the
        exact bug this method exists to close.
        """
        scope = document or self.sketch_scope()
        aliases = self.sketch_aliases
        current = name
        seen = {name}
        found = False
        while True:
            following = aliases.get(f"{scope}::{current}")
            if not following or following == current or following in seen:
                return current if found else None
            seen.add(following)
            current = following
            found = True

    def sketch_aliases_of(self, name: str,
                          document: str | None = None) -> list[str]:
        """Every old name that now resolves to ``name``, sorted.

        Used by the listings: a rebuilt sketch is usable under its old name, so a
        list that prints only the live names tells the caller its sketch is gone
        (reported 2026-10-27: 草图.4 was missing from both ``sketches[]`` and the
        error's ``Available:`` line, and yet worked).
        """
        scope = document or self.sketch_scope()
        found = [key.split("::", 1)[1] for key, target in self.sketch_aliases.items()
                 if key.startswith(f"{scope}::")
                 and (target == name or self.sketch_alias(key.split("::", 1)[1],
                                                          document=scope) == name)]
        return sorted(set(found))

    # ── Document names: one definition, used by every tool ──

    @staticmethod
    def _same_path(left: str, right: str) -> bool:
        """Do two spellings name the same file? Separators and case do not matter."""
        if not left or not right:
            return False
        try:
            import os

            return os.path.normcase(os.path.abspath(left)) == \
                os.path.normcase(os.path.abspath(right))
        except Exception:  # noqa: BLE001 - an unparseable path is just a string
            return left.replace("/", "\\").casefold() == right.replace("/", "\\").casefold()

    def _document_rows(self) -> list[dict[str, Any]]:
        """Every open document, as plain data: name, stem, path, active flag.

        Plain dicts, not COM objects, because the interesting answer is "which
        ones matched" and it has to be publishable in an error reply.

        ``name`` and ``path`` are two different readings and both are needed:
        ``Document.Name`` is the FILE NAME (``DUP35.CATPart``) even for a saved
        document, while ``Document.FullName`` is the path - and an unsaved
        document reports its name as its FullName too. That is what makes a
        duplicate pair tellable apart (B32 acceptance, 2026-10-28: two entries
        printed the same name and only ``path=`` differed).

        Failures reach the caller: "I could not read the Documents collection" is
        not "no document matches", and folding the two together here would answer
        DOCUMENT_NOT_FOUND for an unreadable session. The advisory callers wrap
        this in their own try/except; the resolvers do not.
        """
        rows: list[dict[str, Any]] = []
        docs = self.documents
        count = docs.Count
        active = None
        active_doc = None
        try:
            active_doc = self.active_document
            active = str(active_doc.Name)
        except Exception:  # noqa: BLE001 - no active document is normal
            active = None
        for i in range(1, count + 1):
            doc = docs.Item(i)
            try:
                name = str(doc.Name)
            except Exception:  # noqa: BLE001 - a broken item is not a match
                continue
            try:
                path = str(doc.FullName)
            except Exception:  # noqa: BLE001 - never saved
                path = ""
            # Identity first: two documents can share a name, and then a
            # name comparison marks BOTH as active - which is how the duplicate
            # listing ends up telling the caller nothing.
            is_active = None
            if active_doc is not None:
                try:
                    is_active = bool(doc == active_doc)
                except Exception:  # noqa: BLE001 - not comparable on this build
                    is_active = None
            if is_active is None:
                is_active = active is not None and (
                    name == active or bool(path) and self._same_path(path, active))
            rows.append({
                "doc": doc,
                "name": name,
                "stem": name.rsplit(".", 1)[0] if "." in name else name,
                "path": path or "",
                # An UNSAVED document reports its name as its FullName, so "this
                # row has a path" and "this row was ever written to disk" are
                # different facts - and only the second one can disambiguate a
                # duplicate pair. Without this, a bare name matched the unsaved
                # copy "by path" and the ambiguity disappeared into a coin toss.
                "saved": bool(path) and path != name,
                "active": is_active,
            })
        return rows

    def document_matches(self, name: str) -> list[dict[str, Any]]:
        """Open documents that ``name`` could mean - ALL of them, not the first.

        The first version of every caller returned on the first hit and documented
        the reason: "a name is unambiguous". That premise was false, and this
        server is what breaks it - ``catia_new_part(name=...)`` sets the part
        number, CATIA uses it as the document name, so creating a part under a
        name that is already open leaves two documents with the SAME ``Name``.
        Then ``document='DUP35.CATPart'`` answered with the other model's mass
        properties and echoed the name back (reported 2026-10-28, P1): no error,
        no hint, a plausible wrong number. Collecting every match is the only way
        a caller can be told.
        """
        key = (name or "").strip().casefold()
        if not key:
            return []
        found = []
        for row in self._document_rows():
            if row["name"].casefold() == key or row["stem"].casefold() == key:
                found.append(row)
            elif row["saved"] and self._same_path(row["path"], key):
                found.append(row)
        return found

    def resolve_document(self, name: str) -> tuple[Any | None, list[dict[str, Any]]]:
        """``(document, matches)`` - the document is None when the name is ambiguous.

        Resolution order, and why each step exists:

        1. a PATH match beats a name match. ``document='DUP35'`` matches both
           copies of ``DUP35.CATPart``, but ``document='E:\\...\\DUP35.CATPart'``
           matches only the saved one, so the path is a real answer even when the
           names collide - and it is the answer ``catia_list_documents`` prints;
        2. one match is that match - no notes, no cost;
        3. anything else is ambiguous, and guessing is the behaviour being fixed.

        The caller decides what to say; this only refuses to choose.
        """
        matches = self.document_matches(name)
        key = (name or "").strip()
        by_path = [row for row in matches
                   if row["saved"] and self._same_path(row["path"], key)]
        if len(by_path) == 1:
            return by_path[0]["doc"], matches
        if len(matches) == 1:
            return matches[0]["doc"], matches
        return None, matches

    @staticmethod
    def describe_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The publishable part of ``document_matches``: no COM handles.

        ``path`` is what makes a duplicate pair actionable, and ``saved`` says
        whether the copy has been written to disk - an unsaved document reports
        its name as its path, so it has no path of its own to name it by.
        """
        return [{"document": row["name"],
                 "path": row["path"] if row["saved"] else None,
                 "active": row["active"],
                 "saved": row["saved"]}
                for row in matches]

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
