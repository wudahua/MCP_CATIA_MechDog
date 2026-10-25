"""Document management tools for CATIA V5.

Create, open, save, close, and list documents (Part, Product, Drawing).
"""

from __future__ import annotations

import json
import os
from typing import Any

from catia_mcp import dialogs
from catia_mcp.connection import CATIAConnection


class DocumentTools:
    """Tools for managing CATIA V5 documents."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_connect",
                "description": (
                    "Connect to CATIA V5. Attaches to a running instance or launches a new one. "
                    "Must be called before any other CATIA tool."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "catia_disconnect",
                "description": "Disconnect from CATIA V5 (does not close CATIA).",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "catia_new_part",
                "description": (
                    "Create a new empty Part document in CATIA V5. "
                    "A Part is used for single-body 3D modeling (sketches + features)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Optional name for the part",
                        },
                    },
                },
            },
            {
                "name": "catia_new_product",
                "description": (
                    "Create a new empty Product (assembly) document in CATIA V5. "
                    "A Product is used to assemble multiple Parts together."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Optional name for the product/assembly",
                        },
                    },
                },
            },
            {
                "name": "catia_open_document",
                "description": "Open an existing CATIA document from a file path (.CATPart, .CATProduct, .CATDrawing).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Full path to the CATIA document to open",
                        },
                    },
                    "required": ["file_path"],
                },
            },
            {
                "name": "catia_save_document",
                "description": (
                    "Save the active CATIA document. Optionally save to a new path (Save As). "
                    "Retries around CATIA's modal dialogs (overwrite prompt, first-SaveAs "
                    "dialog), which would otherwise freeze the worker thread. "
                    "With file_path this is Save As. On V5-6R2020 B30 the raw "
                    "Document.SaveAs refused a part that already had geometry (E_FAIL), "
                    "but this tool goes through the FileSystem service instead of the raw "
                    "call, and on V5-6R2022 B32 SP6 a part with 3 bodies and 35 features "
                    "saved-as in one call (measured by the reporter 2026-10-24). Try it "
                    "before assuming you must rebuild: if CATIA refuses, nothing is lost. "
                    "The order that always works is still to save the empty part to its "
                    "final path right after catia_new_part, then model, then call this "
                    "tool with no file_path - that goes through doc.Save(). "
                    "OVERWRITE IS REFUSED BY DEFAULT: if file_path already exists "
                    "and is not this document's own file, the call stops and names "
                    "the file (size, last modified) instead of replacing it. Pass "
                    "overwrite=true to mean it. Added after a verification run "
                    "saved a new empty part onto a finished P03_prop.CATPart and "
                    "lost it - the overwrite had been silent (reported 2026-10-25)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Optional new file path for Save As",
                        },
                        "overwrite": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Replace an existing file at file_path. Without "
                                "it, an existing file is reported instead of "
                                "overwritten."
                            ),
                        },
                    },
                },
            },
            {
                "name": "catia_save_as_copy",
                "description": (
                    "Save the active document, then copy the saved file to another path. "
                    "Use this to keep a snapshot once the model already has geometry: "
                    "catia_save_document's Save As is refused after the first solid exists "
                    "(E_FAIL on V5-6R2020 B30), but doc.Save() followed by a file-system "
                    "copy is not. The open document keeps its original path, so the working "
                    "file is untouched."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Target path for the copy (.CATPart/.CATProduct).",
                        },
                        "overwrite": {
                            "type": "boolean",
                            "description": "Replace the target if it exists (default true).",
                            "default": True,
                        },
                    },
                    "required": ["file_path"],
                },
            },
            {
                "name": "catia_close_document",
                "description": "Close the active CATIA document.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "save": {
                            "type": "boolean",
                            "description": "Whether to save before closing (default: false)",
                            "default": False,
                        },
                    },
                },
            },
            {
                "name": "catia_list_documents",
                "description": "List all open documents in CATIA V5 with their types and paths.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "catia_get_active_document_info",
                "description": (
                    "Get detailed info about the active CATIA document: "
                    "name, type, path, part bodies, features, etc."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "catia_activate_document",
                "description": (
                    "Bring an already-open document to the front so the other tools act "
                    "on it. With a Part and a Product open at once there was no way back "
                    "to the Part except closing the Product (reported 2026-10-23). "
                    "Matches the document name exactly first, then as a stem, then both "
                    "again ignoring case, so 'P05_stand.CATPart', 'P05_stand' and "
                    "'p05_STAND' all work."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Document name, as listed by catia_list_documents",
                        },
                    },
                    "required": ["name"],
                },
            },
            {
                "name": "catia_set_part_number",
                "description": (
                    "Set the part number (零件编号) of the active part document, or of one "
                    "component when component_name is given. Every part built from the "
                    "same template carries the template's number, so an assembly of 24 "
                    "distinct parts can end up with 24 items all called 'Part1' — CATIA "
                    "then reports '零件编号 Part1 已存在。在装配中，零件编号必须是唯一的'. "
                    "The number, not the instance name, is what the BOM and "
                    "catia_list_components report."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "part_number": {
                            "type": "string",
                            "description": "New part number, e.g. 'drone stand'",
                        },
                        "component_name": {
                            "type": "string",
                            "description": (
                                "Optional: set this component's number inside the active "
                                "product instead of the whole document's"
                            ),
                        },
                    },
                    "required": ["part_number"],
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        match tool_name:
            case "catia_connect":
                return self.conn.connect()
            case "catia_disconnect":
                return self.conn.disconnect()
            case "catia_new_part":
                return self._new_part(arguments.get("name"))
            case "catia_new_product":
                return self._new_product(arguments.get("name"))
            case "catia_open_document":
                return self._open_document(arguments["file_path"])
            case "catia_save_document":
                return self._save_document(arguments.get("file_path"),
                                           bool(arguments.get("overwrite", False)))
            case "catia_save_as_copy":
                return self._save_as_copy(
                    arguments["file_path"], bool(arguments.get("overwrite", True)))
            case "catia_close_document":
                return self._close_document(arguments.get("save", False))
            case "catia_list_documents":
                return self._list_documents()
            case "catia_get_active_document_info":
                return self._get_active_document_info()
            case "catia_activate_document":
                return self._activate_document(arguments["name"])
            case "catia_set_part_number":
                return self._set_part_number(
                    arguments["part_number"], arguments.get("component_name"))
            case _:
                raise ValueError(f"Unknown document tool: {tool_name}")

    def _new_part(self, name: str | None = None) -> str:
        self.conn.ensure_connected()
        docs = self.conn.documents
        doc = docs.Add("Part")
        # A fresh part has no sketches; carrying the previous part's would make
        # the first pad/pocket in this document use the wrong profile.
        self.conn.last_sketch = None
        self.conn.active_body = None
        part = self.conn.part_of(doc)
        # Part.Name is read-only on V5-6R2020/B32 (measured: "方法 Name 失败"), so
        # `name` cannot rename the part. What it CAN do is set 零件编号 - the field
        # the BOM and every assembly tree actually show - so do that instead of
        # reporting a half-success.
        note = ""
        part_number = None
        if name:
            try:
                product = doc.Product
                product.PartNumber = name
                part_number = name
                note = (f" Part number set to '{name}' (Part.Name is read-only on "
                        "V5, so this is what the tree and the BOM will show).")
            except Exception as exc:  # noqa: BLE001
                note = (f" Could not set the part number either ({exc}); rename "
                        "the instance later with catia_rename_component.")
        part_name = part.Name
        self.conn.refresh_display()
        hint = (" Save it now - catia_save_document(file_path='...') - because a "
                "part that has geometry but no path cannot be saved later.")
        return (f"Created new Part document: '{part_name}'"
                + (f" [{part_number}]" if part_number else "")
                + f".{note}{hint}")

    def _new_product(self, name: str | None = None) -> str:
        self.conn.ensure_connected()
        docs = self.conn.documents
        doc = docs.Add("Product")
        product = self.conn.product_of(doc)
        rename_note = ""
        if name:
            # Product.Name is the INSTANCE name and is read-only on the root of a
            # new product on B30 (it accepted the assignment and kept 'Product1'),
            # which is why the parameter looked like a no-op for three releases
            # (reported 2026-10-24). PartNumber is writable - catia_set_part_number
            # uses it - so try that first and only then the name.
            for attribute in ("PartNumber", "Name"):
                try:
                    setattr(product, attribute, name)
                except Exception as exc:  # noqa: BLE001 - try the next attribute
                    rename_note += (
                        f" ({attribute} not settable: {str(exc).splitlines()[0][:60]})")
                    continue
                try:
                    applied = str(getattr(product, attribute))
                except Exception:  # noqa: BLE001
                    applied = ""
                if applied == name:
                    rename_note = f" ({attribute} set to '{name}')"
                    break
            else:
                rename_note = (
                    f" (CATIA kept '{product.Name}': neither PartNumber nor Name "
                    f"accepted '{name}' on this build - rename it after saving with "
                    "catia_set_part_number)")
        product_name = product.Name
        try:
            doc_name = str(doc.Name)
        except Exception:  # noqa: BLE001
            doc_name = "the new document"
        self.conn.refresh_display()
        # The old sentence ended "an unsaved assembly is always 'Product1.CATProduct'",
        # which stopped being true once PartNumber started working: the document
        # is now named after it. Saying both in one breath made the reply
        # contradict itself (reported 2026-10-24, twice).
        return (
            f"Created a Product document '{doc_name}' whose root product is "
            f"'{product_name}'{rename_note}. In this session the document is called "
            f"'{doc_name}'; a path you save it to decides the FILE name "
            "(catia_save_document(file_path=...))."
        )

    def _open_document(self, file_path: str) -> str:
        self.conn.ensure_connected()
        docs = self.conn.documents
        # CATIA's Documents.Open rejects forward-slash paths (E_FAIL);
        # normalize to native Windows separators first.
        doc = docs.Open(os.path.normpath(file_path))
        # Whatever sketch belonged to the previously active document does not
        # belong to this one.
        self.conn.last_sketch = None
        self.conn.active_body = None
        return f"Opened document: '{doc.Name}' from {file_path}"

    @staticmethod
    def _document_has_path(doc: Any) -> bool:
        """True when the document already lives in a file (Save has a target)."""
        try:
            full = doc.FullName or ""
        except Exception:  # noqa: BLE001 - unsaved docs may have no FullName
            return False
        return bool(full) and os.path.isabs(full)

    def _document_at_path(self, path: str) -> Any:
        """The open document whose file is ``path`` (None when nothing holds it)."""
        import os

        wanted = os.path.normcase(os.path.abspath(path))
        docs = self.conn.documents
        for i in range(1, docs.Count + 1):
            doc = docs.Item(i)
            try:
                if os.path.normcase(os.path.abspath(str(doc.FullName))) == wanted:
                    return doc
            except Exception:  # noqa: BLE001 - never saved
                continue
        return None

    def _save_document(self, file_path: str | None = None,
                       overwrite: bool = False) -> str:
        doc = self.conn.active_document
        if file_path:
            # SaveAs onto the file of a document that is already open (very often
            # a part loaded as a component of an open assembly) cannot work: the
            # overwrite needs the file and the open document holds it. CATIA says
            # so by failing, six times, ~14 s later (reported 2026-10-25). Name
            # the holder instead of retrying into the same wall.
            holder = self._document_at_path(file_path)
            if holder is not None:
                try:
                    same = str(holder.Name) == str(doc.Name)
                except Exception:  # noqa: BLE001
                    same = False
                if same:
                    # Asking to save a document onto its own file is a Save.
                    name = dialogs.save(doc)
                    return f"Document '{name}' saved"
                users = self._component_usage().get(str(holder.Name)) or []
                raise RuntimeError(
                    f"'{file_path}' is the file of '{holder.Name}', which is open in "
                    "this session"
                    + (f" as a component of {', '.join(users)}" if users else "")
                    + ". CATIA cannot Save As onto a file that is open (or held by an "
                    "open assembly): the overwrite needs the file and the open "
                    "document has it, and the call then retries six times before "
                    "failing. Close that document - or the assembly that references "
                    "it - and save again, or write to a different path."
                )
            replaced = self._existing_file_note(file_path)
            if replaced and not overwrite:
                raise RuntimeError(
                    f"'{file_path}' already exists ({replaced}) and is not this "
                    "document's file. Saving there would replace it, and a "
                    "verification run that did exactly that lost a finished part "
                    "(reported 2026-10-25). Pass overwrite=true if you mean to "
                    "replace it, or choose another path."
                )
            # CATIA's SaveAs rejects forward-slash paths (E_FAIL) and pops a
            # modal prompt when the target exists, which freezes the STA worker.
            target = dialogs.save_as(doc, file_path)
            note = f" (overwrote an existing file, {replaced})" if replaced else ""
            return f"Document saved as: {target}{note}"
        if not self._document_has_path(doc):
            # doc.Save() on a never-saved document raises E_FAIL and retrying it
            # is pure delay: there is no path to write to, and SaveAs is refused
            # once the part carries geometry. Say that instead of burning three
            # attempts and reporting "Save failed after 3 attempts".
            raise RuntimeError(
                f"'{doc.Name}' has never been saved, so there is no file for Save "
                "to overwrite. Pass file_path to this same call - everything built "
                "so far is still in memory and Save As it now. (On B30 a SaveAs "
                "was refused once the part carried geometry; the FileSystem route "
                "this tool uses did succeed on B32 SP6 with 35 features, so it is "
                "worth trying - if it is refused, nothing is lost. Giving a new "
                "part its path right after catia_new_part avoids the question.)"
            )
        name = dialogs.save(doc)
        return f"Document '{name}' saved"

    @staticmethod
    def _existing_file_note(path: str) -> str | None:
        """``"12.3 KB, 2026-10-25 09:14"`` when the file is already there."""
        import datetime
        import os

        try:
            if not os.path.isfile(path):
                return None
            stat = os.stat(path)
        except OSError:
            return None
        size = stat.st_size
        for unit in ("B", "KB", "MB"):
            if size < 1024 or unit == "MB":
                amount = f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
                break
            size /= 1024.0
        stamp = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        return f"{amount}, last modified {stamp}"

    def _save_as_copy(self, file_path: str, overwrite: bool = True) -> str:
        """Save, then copy the file - the only way to snapshot a finished model.

        SaveAs is refused once the document holds geometry, so copying the saved
        file through CATIA's own FileSystem service is what is left. The open
        document keeps its path.
        """
        import os

        doc = self.conn.active_document
        # A document that has never been saved has no folder, and CATIA cannot
        # be given one once geometry exists - doc.Save() just raises the same
        # "method failed" as SaveAs. Say so instead of failing obscurely.
        try:
            current = doc.FullName
        except Exception:
            current = ""
        if not current or not os.path.dirname(current):
            raise RuntimeError(
                "This document has never been saved, so it has no path to copy from. "
                "CATIA only accepts a path before geometry exists: create the part, "
                "call catia_save_document(file_path=...) on the empty part, then build "
                "and call catia_save_as_copy again."
            )
        dialogs.save(doc)
        source = doc.FullName
        target = os.path.normpath(os.path.abspath(file_path))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        try:
            fs = self.conn.app.FileSystem
            fs.CopyFile(os.path.normpath(source), target, bool(overwrite))
        except Exception:
            # Same job without the CATIA service; a plain copy of a saved
            # document is identical.
            import shutil

            shutil.copyfile(source, target)
        return (
            f"Copied '{os.path.basename(source)}' -> '{target}'. "
            f"The open document still points at '{source}'."
        )

    def _document_names(self) -> list[str]:
        """Every open document name, read defensively."""
        try:
            docs = self.conn.documents
            return [str(docs.Item(i).Name) for i in range(1, docs.Count + 1)]
        except Exception:  # noqa: BLE001 - diagnostics only
            return []

    def _close_document(self, save: bool = False) -> str:
        """Close the active document, and check that it actually closed.

        ``doc.Close()`` returning without an exception is not evidence: CATIA
        silently keeps a document that another open document still references,
        and the old reply said "closed" anyway. The reporter then hit the same
        locked part again on the next SaveAs and spent a long time on it
        (2026-10-24). The count before/after is the only honest check.
        """
        doc = self.conn.active_document
        name = doc.Name
        if save:
            dialogs.save(doc)
        before = self._document_names()
        doc.Close()
        after = self._document_names()
        still = after.count(name) >= max(1, before.count(name))
        if still:
            return (
                f"CLOSE NOT CONFIRMED: '{name}' is still open after Close() "
                f"({before.count(name)} entry/entries before, {after.count(name)} "
                "after). CATIA keeps a document that an open assembly references, "
                "so close the referencing assembly first "
                "(catia_activate_document, then close), then close this one. "
                "Until then a save of this part fails with '方法 SaveAs 失败' and "
                "the reason looks unrelated to the assembly."
            )
        return f"Document '{name}' closed" + (" (saved)" if save else "")

    def _list_documents(self) -> str:
        """Every document in the session, and which assemblies use it.

        A part that exists only as a component of an open product IS in this
        collection, and it cannot be activated - so listing it beside real
        windows left the caller with two messages that contradict each other
        ("it is open" / "open it first"). Reproduced by a reporter who wrote the
        wrong call order from exactly this listing (2026-10-24). ``component_of``
        says which open assemblies reference the part; that is a fact this tool
        can read without touching the session, whereas "activatable" would need
        an Activate() per document.
        """
        self.conn.ensure_connected()
        docs = self.conn.documents
        uses = self._component_usage()
        result = []
        for i in range(1, docs.Count + 1):
            doc = docs.Item(i)
            doc_info = {
                "name": doc.Name,
                "path": doc.FullName if hasattr(doc, "FullName") else "unsaved",
            }
            # Detect document type; must go through the dynamic re-wrap, because a
            # gen_py-typed Document exposes only the base interface.
            try:
                self.conn.part_of(doc)
                doc_info["type"] = "CATPart"
            except Exception:
                try:
                    self.conn.product_of(doc)
                    doc_info["type"] = "CATProduct"
                except Exception:
                    doc_info["type"] = "Other"
            if doc_info["type"] == "CATPart" and str(doc.Name) in uses:
                doc_info["component_of"] = uses[str(doc.Name)]
                doc_info["note"] = (
                    "used by an open assembly, so it may have no window of its own; "
                    "if catia_activate_document refuses it, open it with "
                    "catia_open_document (or measure it by name with document=)")
            result.append(doc_info)

        if not result:
            return "No documents open in CATIA"
        return json.dumps(result, indent=2, ensure_ascii=False)

    def _component_usage(self) -> dict[str, list[str]]:
        """Part document name -> the open assemblies whose components use it."""
        uses: dict[str, list[str]] = {}
        docs = self.conn.documents
        for i in range(1, docs.Count + 1):
            product_doc = docs.Item(i)
            try:
                product = self.conn.product_of(product_doc)
                children = product.Products
            except Exception:  # noqa: BLE001 - not a product
                continue
            for j in range(1, children.Count + 1):
                try:
                    child = children.Item(j)
                    part_doc = child.ReferenceProduct.Parent
                    name = str(part_doc.Name)
                except Exception:  # noqa: BLE001 - a dead link
                    continue
                uses.setdefault(name, [])
                owner = str(product_doc.Name)
                if owner not in uses[name]:
                    uses[name].append(owner)
        return uses

    def _activate_document(self, name: str) -> str:
        """Activate an open document by name so later calls act on it.

        Four tiers, tried in order: the document name as given, the same name
        without its extension, then both again case-folded. The last tier is
        not decoration: ``catia_new_part(name="CaseTest")`` followed by a save
        to ``casetest.CATPart`` gives a document whose only name is the file's,
        and the string the caller still holds is the one they created.
        """
        self.conn.ensure_connected()
        docs = self.conn.documents
        names = []
        key = (name or "").casefold()
        tiers: list[Any] = [None, None, None, None]
        for i in range(1, docs.Count + 1):
            doc = docs.Item(i)
            names.append(doc.Name)
            stem = doc.Name.split(".")[0]
            if tiers[0] is None and doc.Name == name:
                tiers[0] = doc
            if tiers[1] is None and stem == name:
                tiers[1] = doc
            if tiers[2] is None and doc.Name.casefold() == key:
                tiers[2] = doc
            if tiers[3] is None and stem.casefold() == key:
                tiers[3] = doc
        target = next((doc for doc in tiers if doc is not None), None)
        if target is None:
            raise ValueError(
                f"No open document named '{name}'. Open: {', '.join(names) or 'none'}."
            )
        target.Activate()
        # Read the state back. Activate() returning without an exception means
        # "the call was accepted", not "the document is now active": a part that
        # exists only as a component of an open assembly is reachable as a
        # document object, accepts Activate(), and leaves ActiveDocument on the
        # product - so every later call silently measured the ASSEMBLY instead of
        # the part (reported 2026-10-24 on B32, twice, with get_inertia returning
        # 16000 mm3 for an 8000 mm3 part as the proof).
        self.conn.refresh_display()
        try:
            active = str(self.conn.app.ActiveDocument.Name)
        except Exception:  # noqa: BLE001 - nothing active at all
            active = ""
        if active and active != str(target.Name):
            if active.casefold().endswith(".catproduct"):
                raise RuntimeError(
                    f"CATIA did not switch to '{target.Name}': ActiveDocument is "
                    f"still '{active}'. A part that exists only as a component of "
                    "the open product cannot be made active by name on this build - "
                    "it is being edited in context, not opened as a window. Two "
                    "routes that work: (a) measure it by name without activating "
                    f"anything - catia_get_inertia(document='{target.Name}') or "
                    f"catia_get_bounding_box(document='{target.Name}', "
                    "method='mesh'); (b) open the part as its own document with "
                    f"catia_open_document, then activate it."
                )
            raise RuntimeError(
                f"CATIA accepted Activate() but ActiveDocument is '{active}', not "
                f"'{target.Name}'. The document was not switched, so any call you "
                "make now acts on the wrong document."
            )
        return f"Activated document: '{target.Name}'"

    def _set_part_number(self, part_number: str, component_name: str | None = None) -> str:
        """Set 零件编号 on the active part, or on one component of the active product."""
        self.conn.ensure_connected()
        part_number = (part_number or "").strip()
        if not part_number:
            raise ValueError("part_number must not be empty")
        doc = self.conn.active_document

        if component_name:
            product = self.conn.product_of(doc)
            products = product.Products
            target, index = None, 0
            for i in range(1, products.Count + 1):
                if products.Item(i).Name == component_name:
                    target, index = products.Item(i), i
                    break
            if target is None:
                available = [products.Item(i).Name for i in range(1, products.Count + 1)]
                raise ValueError(f"No component '{component_name}'. Available: {available}")
            old = target.PartNumber
            target.PartNumber = part_number
            note = ""
            if old != part_number:
                note = (
                    " A component's number lives in the part it references, so every "
                    "instance of that file now reports the same new number."
                )
            clashes = [
                products.Item(i).Name
                for i in range(1, products.Count + 1)
                if i != index and products.Item(i).PartNumber == part_number
            ]
            if clashes:
                note += f" Still duplicated with: {', '.join(clashes)}."
            self.conn.refresh_display()
            return f"'{component_name}' part number: '{old}' -> '{part_number}'.{note}"

        # The late-bound Part exposes no PartNumber, so a part's 零件编号 lives on
        # the implicit product every PartDocument carries.
        try:
            product = doc.Product
            old = product.PartNumber
            product.PartNumber = part_number
            kind = "part number (on the part document's implicit product)"
        except Exception:
            product = self.conn.product_of(doc)
            old = product.PartNumber
            product.PartNumber = part_number
            kind = "product number"
        self.conn.refresh_display()
        return f"Document {kind}: '{old}' -> '{part_number}'"

    def _get_active_document_info(self) -> str:
        doc = self.conn.active_document
        info: dict[str, Any] = {
            "name": doc.Name,
            "path": doc.FullName if hasattr(doc, "FullName") else "unsaved",
        }

        # Try Part document
        try:
            part = self.conn.part_of(doc)
            info["type"] = "CATPart"
            info["part_name"] = part.Name
            try:
                info["part_number"] = doc.Product.PartNumber
            except Exception:
                pass

            # List bodies
            bodies = part.Bodies
            body_list = []
            for i in range(1, bodies.Count + 1):
                body = bodies.Item(i)
                shapes = []
                for j in range(1, body.Shapes.Count + 1):
                    shapes.append(body.Shapes.Item(j).Name)
                body_list.append({
                    "name": body.Name,
                    "features": shapes,
                })
            info["bodies"] = body_list

            # List geometrical sets (hybrid bodies)
            try:
                hbs = part.HybridBodies
                geo_sets = []
                for i in range(1, hbs.Count + 1):
                    geo_sets.append(hbs.Item(i).Name)
                info["geometrical_sets"] = geo_sets
            except Exception:
                pass

            # Parameters count
            try:
                info["parameters_count"] = part.Parameters.Count
            except Exception:
                pass

            return json.dumps(info, indent=2, ensure_ascii=False)
        except Exception:
            pass

        # Try Product document
        try:
            product = doc.Product
            info["type"] = "CATProduct"
            info["product_name"] = product.Name
            info["part_number"] = product.PartNumber

            # List sub-products
            prods = product.Products
            children = []
            for i in range(1, prods.Count + 1):
                child = prods.Item(i)
                children.append({
                    "name": child.Name,
                    "part_number": child.PartNumber,
                })
            info["components"] = children
            return json.dumps(info, indent=2, ensure_ascii=False)
        except Exception:
            pass

        info["type"] = "Unknown"
        return json.dumps(info, indent=2, ensure_ascii=False)
