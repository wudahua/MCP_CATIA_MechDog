"""Assembly tools for CATIA V5.

Product/Assembly management: add components, constraints (Fix, Coincidence,
Contact, Offset, Angle), move components, and manage the product tree.
"""

from __future__ import annotations

import json
import os
from typing import Any

from catia_mcp.connection import CATIAConnection


# `Position.GetComponents` fills 12 by-ref slots; pywin32 cannot call that
# directly (it fails with "方法 GetComponents 失败"), so it goes through
# SystemService.Evaluate, which is also how the other array reads work here.
POSITION_SCRIPT = (
    "Public Function position_components(p)\n"
    "    Dim a(11)\n"
    "    p.GetComponents a\n"
    "    position_components = a\n"
    "End Function\n"
)


def position_components(app: Any, position: Any) -> list[float]:
    """CATIA's 12 placement numbers: three basis vectors, then the origin."""
    raw = app.SystemService.Evaluate(POSITION_SCRIPT, 0, "position_components",
                                     [position])
    return [float(value) for value in raw]


class AssemblyTools:
    """Tools for assembly (Product) operations in CATIA V5."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_add_component",
                "description": (
                    "Add an existing CATPart or CATProduct file as a component in the "
                    "active assembly. The component is inserted at the origin. The "
                    "insert runs in a separate process attached to the same CATIA, "
                    "because this is the one call that can raise a file dialog on the "
                    "automation thread and freeze the whole server; if it does not "
                    "come back within CATIA_MCP_GUARD_TIMEOUT (60 s) it is killed, "
                    "the dialog is cancelled, and the reply says how many components "
                    "the assembly holds so you can tell whether it happened anyway."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Full path to the .CATPart or .CATProduct file to add",
                        },
                    },
                    "required": ["file_path"],
                },
            },
            {
                "name": "catia_add_new_part",
                "description": (
                    "Create a new empty Part directly inside the active assembly. "
                    "Returns the name of the created component."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name for the new part component",
                        },
                    },
                },
            },
            {
                "name": "catia_fix_constraint",
                "description": (
                    "Fix a component in place (remove all degrees of freedom). "
                    "Typically applied to the base/reference component."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "component_name": {
                            "type": "string",
                            "description": "Name of the component to fix",
                        },
                    },
                    "required": ["component_name"],
                },
            },
            {
                "name": "catia_coincidence_constraint",
                "description": (
                    "Create a Coincidence constraint between two components. "
                    "Aligns axes, planes, or points of two components."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "component1": {
                            "type": "string",
                            "description": "Name of first component",
                        },
                        "component2": {
                            "type": "string",
                            "description": "Name of second component",
                        },
                        "element1": {
                            "type": "string",
                            "description": "Geometry element on component1 (e.g., 'xy plane', 'Face.1')",
                        },
                        "element2": {
                            "type": "string",
                            "description": "Geometry element on component2",
                        },
                    },
                    "required": ["component1", "component2"],
                },
            },
            {
                "name": "catia_offset_constraint",
                "description": (
                    "Create an Offset constraint between two faces/planes of two components. "
                    "Maintains a constant distance between the reference elements."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "component1": {
                            "type": "string",
                            "description": "Name of first component",
                        },
                        "component2": {
                            "type": "string",
                            "description": "Name of second component",
                        },
                        "offset": {
                            "type": "number",
                            "description": "Offset distance in mm",
                        },
                    },
                    "required": ["component1", "component2", "offset"],
                },
            },
            {
                "name": "catia_angle_constraint",
                "description": "Create an Angle constraint between two components.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "component1": {
                            "type": "string",
                            "description": "Name of first component",
                        },
                        "component2": {
                            "type": "string",
                            "description": "Name of second component",
                        },
                        "angle": {
                            "type": "number",
                            "description": "Angle in degrees",
                        },
                    },
                    "required": ["component1", "component2", "angle"],
                },
            },
            {
                "name": "catia_move_component",
                "description": (
                    "Move a component by translation and/or rotation. "
                    "Translation in mm, rotation in degrees."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "component_name": {
                            "type": "string",
                            "description": "Name of the component to move",
                        },
                        "tx": {"type": "number", "description": "Translation X (mm)", "default": 0},
                        "ty": {"type": "number", "description": "Translation Y (mm)", "default": 0},
                        "tz": {"type": "number", "description": "Translation Z (mm)", "default": 0},
                        "rx": {"type": "number", "description": "Rotation around X (degrees)", "default": 0},
                        "ry": {"type": "number", "description": "Rotation around Y (degrees)", "default": 0},
                        "rz": {"type": "number", "description": "Rotation around Z (degrees)", "default": 0},
                    },
                    "required": ["component_name"],
                },
            },
            {
                "name": "catia_rename_component",
                "description": (
                    "Rename a component INSTANCE inside the active assembly. This is the "
                    "only way to get a readable tree on CATIA V5-6R2020: the part's own "
                    "Name is read-only there (Part.Name raises E_NOTIMPL), so every part "
                    "added from file arrives as Part1.1, Part2.1, ... regardless of the "
                    "file name. The instance name is writable."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "component_name": {
                            "type": "string",
                            "description": "Component name, or its 1-based index as a string",
                        },
                        "new_name": {
                            "type": "string",
                            "description": "Instance name to set, e.g. 'P01_CylinderBlock'",
                        },
                    },
                    "required": ["component_name", "new_name"],
                },
            },
            {
                "name": "catia_list_components",
                "description": "List all components in the active assembly/product with their names and positions.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "catia_list_constraints",
                "description": "List all assembly constraints in the active product.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "catia_get_component_transform",
                "description": (
                    "Read a component's absolute placement as an explicit rotation + "
                    "translation, with the rotation matrix in BOTH conventions. Native "
                    "CATIA stores the nine rotation values as the three basis vectors "
                    "(columns of R, i.e. where local X/Y/Z point in the assembly); "
                    "specs usually quote R row-major. Returns det and orthogonality so a "
                    "transpose mistake is visible immediately."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "component_name": {
                            "type": "string",
                            "description": "Component name, or its 1-based index as a string",
                        },
                    },
                    "required": ["component_name"],
                },
            },
            {
                "name": "catia_set_component_transform",
                "description": (
                    "Set a component's absolute placement from a rotation matrix and a "
                    "translation. `rotation_rows` is 3x3; say with `rotation_convention` "
                    "which form it is: 'matrix' (default) reads it row-major as A in "
                    "P_asm = A·P_local + T, while 'basis' reads row i as 'where does local "
                    "axis i point in global' — CATIA's own form, the form reverse-engineered "
                    "spec tables list, and the form whose maths is P_asm = Rᵀ·P_local + T. "
                    "Pass either screenshot-verified form verbatim; picking the wrong one "
                    "yields a valid-looking placement that is the transpose of what you "
                    "asked for. `position_components` takes CATIA's raw 12 numbers instead. "
                    "Reads the placement back and reports deviation, det, orthogonality and "
                    "where each local axis actually ended up."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "component_name": {
                            "type": "string",
                            "description": "Component name, or its 1-based index as a string",
                        },
                        "rotation_rows": {
                            "type": "array",
                            "description": (
                                "3x3 rotation; see rotation_convention. With 'basis' each "
                                "row is where one local axis points in global."
                            ),
                            "items": {"type": "array", "items": {"type": "number"}},
                        },
                        "rotation_convention": {
                            "type": "string",
                            "enum": ["matrix", "basis"],
                            "description": (
                                "'matrix' (default): rotation_rows is row-major A in "
                                "P_asm = A·P_local + T. 'basis': row i = where local axis i "
                                "points in global, i.e. P_asm = Rᵀ·P_local + T."
                            ),
                        },
                        "position_components": {
                            "type": "array",
                            "description": (
                                "12 numbers in CATIA's native order: "
                                "[i0,i1,i2, j0,j1,j2, k0,k1,k2, Tx,Ty,Tz]"
                            ),
                            "items": {"type": "number"},
                        },
                        "translation": {
                            "type": "array",
                            "description": "Translation (Tx, Ty, Tz) in mm; defaults to 0,0,0",
                            "items": {"type": "number"},
                        },
                        "verify": {
                            "type": "boolean",
                            "description": "Read the placement back and report the deviation",
                            "default": True,
                        },
                    },
                    "required": ["component_name"],
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        match tool_name:
            case "catia_add_component":
                return self._add_component(arguments["file_path"])
            case "catia_add_new_part":
                return self._add_new_part(arguments.get("name"))
            case "catia_fix_constraint":
                return self._fix_constraint(arguments["component_name"])
            case "catia_coincidence_constraint":
                return self._coincidence_constraint(arguments)
            case "catia_offset_constraint":
                return self._offset_constraint(arguments)
            case "catia_angle_constraint":
                return self._angle_constraint(arguments)
            case "catia_move_component":
                return self._move_component(arguments)
            case "catia_rename_component":
                return self._rename_component(arguments)
            case "catia_list_components":
                return self._list_components()
            case "catia_list_constraints":
                return self._list_constraints()
            case "catia_get_component_transform":
                return self._get_component_transform(arguments["component_name"])
            case "catia_set_component_transform":
                return self._set_component_transform(arguments)
            case _:
                raise ValueError(f"Unknown assembly tool: {tool_name}")

    def _add_component(self, file_path: str) -> str:
        """Insert a part file, in a process that a dialog cannot take hostage.

        Measured by the reporter on V5-6R2020 B32 (2026-10-24): this API rejects
        FORWARD-slash paths (``AddComponentsFromFiles`` answers E_FAIL and then
        puts up a modal "打开" dialog), and a modal dialog blocks the COM worker
        for as long as nobody answers it - their call sat there for 2 h 14 min,
        and the old code would have claimed success anyway. So: normalise the
        path to backslashes for this call only (every other tool is fine with
        forward slashes), silence file alerts, and check that a component
        actually appeared before saying so.

        The count check catches the quiet version of the failure. It cannot catch
        the loud one - a dialog that is up keeps the worker blocked, so no check
        ever runs. That is why the call itself happens in a separate process
        (:mod:`catia_mcp.guard`): CATIA is an out-of-process server, a second
        client can attach to the same instance, and a client that does not come
        back can be killed. The dialog is then answered by the recovery sweep,
        and the reply says the call was abandoned instead of the session dying.
        """
        import os

        from catia_mcp import dialogs, guard

        product = self._require_active_product()
        products = product.Products
        # Only this call gets the Windows form; open_document / save_document /
        # export are field-verified with forward slashes and stay untouched.
        native_path = os.path.normpath(file_path)
        activated = getattr(self, "_activated_for_insert", None)
        lead = (f" (activated '{activated}' first: it was the only assembly open "
                "and a Part document was active)") if activated else ""
        before = 0
        try:
            before = products.Count
        except Exception:  # noqa: BLE001 - the comparison is best-effort
            pass

        if guard.enabled():
            try:
                active_name = str(self.conn.active_document.Name)
            except Exception:  # noqa: BLE001
                active_name = None
            outcome = guard.run("add_components",
                                {"path": native_path,
                                 "active_document": active_name},
                                seconds=guard.timeout())
            if outcome.timed_out:
                # The child was killed, so CATIA is ours again; the dialog it
                # raised is not. Cancel it, then look at what actually happened.
                clicks = guard.cleanup()
                after = before
                try:
                    after = products.Count
                except Exception:  # noqa: BLE001
                    pass
                added = after - before
                raise RuntimeError(
                    f"catia_add_component was abandoned after "
                    f"{outcome.data.get('seconds', guard.timeout()):.0f} s: CATIA put up "
                    "a modal dialog and never returned, so the call was run in a "
                    "separate process and that process was killed. The dialog has "
                    f"been cancelled ({clicks} button(s) pressed). The assembly now "
                    f"holds {after} component(s) - {added} more than before, so the "
                    f"insert {'DID happen' if added > 0 else 'did not happen'}; "
                    "verify with catia_list_components before retrying. Path used: "
                    f"'{native_path}'. If it was a file prompt, the file may not be "
                    "a saved .CATPart, or another application holds it open."
                )
            if not outcome.unavailable:
                if not outcome.ok:
                    raise RuntimeError(
                        f"CATIA did not add the component ({outcome.code}): "
                        f"{outcome.message}. Path used: '{native_path}'."
                        + self._insert_blocked_hint(native_path)
                    )
                after = int(outcome.data.get("after") or before)
                added = outcome.data.get("added") or []
                self.conn.refresh_display()
                return (f"Component added from: {native_path} "
                        f"({after - before} instance(s), assembly now holds {after})"
                        + (f" - {', '.join(added)}" if added else "")
                        + lead
                        + ". The insert ran in an isolated process, so a CATIA "
                          "dialog could not block the session.")
            # The child could not start or could not attach (different desktop,
            # COM not registered for a second client...). Falling back keeps the
            # tool working; it only loses the protection, so say so once the
            # in-process call has run.

        alerts = None
        try:
            alerts = self.conn.app.DisplayFileAlerts
            self.conn.app.DisplayFileAlerts = False
        except Exception:  # noqa: BLE001 - not every build exposes it
            alerts = None
        try:
            with dialogs.watch():
                products.AddComponentsFromFiles([native_path], "All")
        finally:
            if alerts is not None:
                try:
                    self.conn.app.DisplayFileAlerts = alerts
                except Exception:  # noqa: BLE001
                    pass
        after = products.Count
        self.conn.refresh_display()
        if after <= before:
            raise RuntimeError(
                f"CATIA accepted the call but no component appeared (count stayed "
                f"{before}). Path used: '{native_path}'. On this build "
                "AddComponentsFromFiles rejects forward-slash paths and then opens a "
                "modal dialog that blocks the automation thread; if the file is not "
                "a saved .CATPart, save it first - or build it in place with "
                "catia_add_new_part."
            )
        note = ""
        if guard.enabled():
            note = (" (ran in-process: the isolated worker could not attach to "
                    "CATIA, so a dialog would have blocked this call)")
        return (f"Component added from: {native_path} "
                f"({after - before} instance(s), assembly now holds {after}).{note}{lead}")

    def _insert_blocked_hint(self, path: str) -> str:
        """Extra sentence when the refused file is already open in this session.

        Two measurements, and they disagree, so the wording has to carry both.
        On B30 (2026-10-24) inserting a part whose document was open but never
        activated worked, and the same insert failed with
        'AddComponentsFromFiles 失败' right after that document had been
        activated (catia_activate_document, or the temporary activation a tensor
        measurement does). On B32 the reporter could not reproduce it. An open
        document is therefore a *candidate* cause worth naming - not a fact, and
        not something to state as one (corrected 2026-10-25 by the reporter).
        """
        target = os.path.normcase(os.path.abspath(path))
        for i in range(1, self.conn.documents.Count + 1):
            doc = self.conn.documents.Item(i)
            try:
                full = os.path.normcase(os.path.abspath(str(doc.FullName)))
            except Exception:  # noqa: BLE001 - unsaved document
                continue
            if full == target:
                return (
                    f" Note: '{doc.Name}' is already open in this session, which is "
                    "the first thing to rule out - on V5-6R2020 B30 an insert of a "
                    "file whose document had been activated failed this way, while "
                    "the same insert worked before the activation (not reproduced "
                    "on B32, so treat it as a suspect, not a diagnosis). Save it if "
                    "needed, close it (catia_close_document), then insert again - "
                    "the components already in the assembly are untouched."
                )
        return ""

    def _add_new_part(self, name: str | None = None) -> str:
        """Create an empty part component, and say whether the name took.

        ``Name`` is the instance name and is frequently refused on a fresh
        component; ``PartNumber`` is the writable one (same split as
        catia_new_product). Before 0.3.12 this reported CATIA's name and stayed
        silent about the requested one, so a caller that passed 'EMPTY1' and read
        'Part' back had no way to tell whether the argument had been ignored
        (reported 2026-10-24).
        """
        # Not get_active_product(): on a Part document that returns the stub
        # Product every PartDocument carries, and AddNewProduct on the stub
        # fails. Same trap as catia_add_component (reported 2026-10-25).
        product = self._require_active_product()
        products = product.Products
        new_product = products.AddNewProduct("Part")
        note = ""
        if name:
            for attribute in ("Name", "PartNumber"):
                try:
                    setattr(new_product, attribute, name)
                    applied = str(getattr(new_product, attribute))
                except Exception as exc:  # noqa: BLE001 - try the next attribute
                    note = f" ({attribute} not settable: {str(exc).splitlines()[0][:60]})"
                    continue
                if applied == name:
                    note = f" ({attribute} set to '{name}')"
                    break
                note = (f" (requested '{name}' was not applied by CATIA; it kept "
                        f"'{applied}' - rename it with catia_rename_component)")
        self.conn.refresh_display()
        return (f"New Part component created in assembly: '{new_product.Name}'"
                f"{note}.")

    @staticmethod
    def _is_product_document(doc: Any) -> bool:
        """Whether ``doc`` is an assembly, decided by its type - not by ``.Product``.

        ``PartDocument`` answers ``.Product`` too (a stub created for the
        document's single component), so probing for the property proves
        nothing: ``get_active_product()`` happily returns that stub while a Part
        is active, and the insert then fails on it with '_AddComponentsFromFiles
        失败'. Measured on B30 2026-10-25 (reported 2026-10-25).
        """
        try:
            kind = str(doc.Type)
        except Exception:  # noqa: BLE001 - fall back to the file name
            kind = ""
        if kind:
            return kind.upper().endswith("PRODUCT")
        try:
            return str(doc.FullName).upper().endswith(".CATPRODUCT")
        except Exception:  # noqa: BLE001 - unsaved and untyped: assume no
            return False

    def _require_active_product(self) -> Any:
        """The active Product, or a message that names the actual state.

        ``AddComponentsFromFiles`` is a method of ``Products``, so it needs a
        Product document to be active. Measuring a part by name leaves the PART
        active (``document=`` restores what was active before, which is often the
        part), so the very next insert failed with a bare
        'AddComponentsFromFiles 失败' plus a diagnostic about Part Design
        factories - neither of which is true (reported 2026-10-24). If exactly one
        product is open, activate it and say so.

        The type check is what makes the fallback reachable at all: before
        0.3.15 it asked ``get_active_product()`` first, which succeeds on a Part
        document, so "activate the only open assembly for you" never ran and the
        caller got the Part Design diagnostic instead (reported 2026-10-25).
        """
        try:
            active_doc = self.conn.active_document
        except Exception:  # noqa: BLE001 - no document at all
            active_doc = None
        self._activated_for_insert = None
        if active_doc is not None and self._is_product_document(active_doc):
            return self.conn.product_of(active_doc)

        try:
            active = str(active_doc.Name)
        except Exception:  # noqa: BLE001
            active = "<none>"
        open_products = []
        for i in range(1, self.conn.documents.Count + 1):
            doc = self.conn.documents.Item(i)
            if self._is_product_document(doc):
                open_products.append(str(doc.Name))
        if len(open_products) == 1:
            for i in range(1, self.conn.documents.Count + 1):
                doc = self.conn.documents.Item(i)
                if str(doc.Name) == open_products[0]:
                    doc.Activate()
                    # Only one assembly is open, so activating it is unambiguous -
                    # but it changes what the caller has in front of them, so the
                    # reply has to say it happened.
                    self._activated_for_insert = open_products[0]
                    return self.conn.product_of(doc)
        raise RuntimeError(
            f"catia_add_component needs the ASSEMBLY to be the active document, and "
            f"'{active}' is active. A component can only be inserted into a Product. "
            + (f"Open assemblies: {', '.join(open_products)} - activate one with "
               "catia_activate_document." if open_products
               else "No Product document is open - create one with catia_new_product.")
        )

    def _rename_component(self, args: dict[str, Any]) -> str:
        """Rename a component instance and report whether CATIA kept it."""
        product = self.conn.get_active_product()
        component = self._resolve_component(product, str(args["component_name"]))
        before = str(component.Name)
        wanted = str(args["new_name"])
        component.Name = wanted
        after = str(component.Name)
        self.conn.refresh_display()
        if after != wanted:
            return (
                f"Rename not applied: CATIA kept '{after}' instead of '{wanted}' "
                f"(component was '{before}')."
            )
        return f"Component '{before}' renamed to '{after}'."

    _CONSTRAINT_EXPLAIN = (
        "CATIA refused the constraint. Measured on V5-6R2020 B30 (2026-10-23), "
        "constraints cannot be created through this automation surface at all://n"
        "  · AddMonoEltCst (Fix) returns E_FAIL for a component whether it came "
        "from AddComponentsFromFiles or from add_new_part, and whether or not it "
        "is put in the selection first - five argument forms tried, including "
        "the component's ReferenceProduct and the root product;\n"
        "  · AddBiEltCst (Coincidence/Offset/Angle) returns E_FAIL with any "
        "pair of operands, because an assembly constraint needs a Reference into "
        "each component's geometry, and the late-bound Product exposes neither "
        "a .Reference nor a Part document to build one from (in-memory parts "
        "have no PartDocument at all).\n"
        "What to do instead: place components with absolute transforms. "
        "catia_set_component_transform writes the position directly, which is "
        "how a 24-instance quadcopter assembly was placed with a 5e-7 mm "
        "residual; the tree then shows the root as not fixed, and nothing moves "
        "because of it. If you need a parameterised assembly, build it from a "
        "spec table of transforms rather than from constraints."
    )

    def _constraint_failed(self, kind: str, exc: Exception) -> RuntimeError:
        return RuntimeError(f"{kind}: {self._CONSTRAINT_EXPLAIN} Original error: {exc}")

    def _fix_constraint(self, component_name: str) -> str:
        """Fix (reference) one component so the assembly is not floating.

        AddMonoEltCst(0, product) fails with E_FAIL on V5-6R2020 B30 unless the
        component is in the selection first - the same "CATIA needs the object
        selected" rule that bites every constraint API. Field case: placing a
        24-instance quadcopter, every transform applied correctly, then the Fix
        constraint refused with '方法 AddMonoEltCst 失败' and took the whole
        script down with it.
        """
        product = self.conn.get_active_product()
        constraints = product.Connections("CATIAConstraints")

        component = product.Products.Item(component_name)
        selector = self.conn.hso
        try:
            selector.Clear()
            selector.Add(component)
            cst = constraints.AddMonoEltCst(0, component)  # catCstTypeReference
        except Exception as exc:  # noqa: BLE001 - explained below
            raise self._constraint_failed(
                f"Fix constraint on '{component_name}'", exc) from exc
        finally:
            try:
                selector.Clear()
            except Exception:
                pass
        cst.Name = f"Fix.{component_name}"

        self.conn.refresh_display()
        return f"Fix constraint applied to '{component_name}'"

    def _coincidence_constraint(self, args: dict[str, Any]) -> str:
        product = self.conn.get_active_product()
        constraints = product.Connections("CATIAConstraints")

        comp1 = product.Products.Item(args["component1"])
        comp2 = product.Products.Item(args["component2"])

        try:
            cst = constraints.AddBiEltCst(0, comp1, comp2)  # Coincidence
        except Exception as exc:  # noqa: BLE001 - explained by _constraint_failed
            raise self._constraint_failed("Coincidence constraint", exc) from exc
        self.conn.refresh_display()
        return (
            f"Coincidence constraint created between "
            f"'{args['component1']}' and '{args['component2']}'"
        )

    def _offset_constraint(self, args: dict[str, Any]) -> str:
        product = self.conn.get_active_product()
        constraints = product.Connections("CATIAConstraints")

        comp1 = product.Products.Item(args["component1"])
        comp2 = product.Products.Item(args["component2"])

        try:
            cst = constraints.AddBiEltCst(1, comp1, comp2)  # Offset
            cst.Dimension.Value = args["offset"]
        except Exception as exc:  # noqa: BLE001 - explained by _constraint_failed
            raise self._constraint_failed("Offset constraint", exc) from exc

        self.conn.refresh_display()
        return (
            f"Offset constraint: {args['offset']} mm between "
            f"'{args['component1']}' and '{args['component2']}'"
        )

    def _angle_constraint(self, args: dict[str, Any]) -> str:
        product = self.conn.get_active_product()
        constraints = product.Connections("CATIAConstraints")

        comp1 = product.Products.Item(args["component1"])
        comp2 = product.Products.Item(args["component2"])

        try:
            cst = constraints.AddBiEltCst(2, comp1, comp2)  # Angle
            cst.Dimension.Value = args["angle"]
        except Exception as exc:  # noqa: BLE001 - explained by _constraint_failed
            raise self._constraint_failed("Angle constraint", exc) from exc

        self.conn.refresh_display()
        return (
            f"Angle constraint: {args['angle']}° between "
            f"'{args['component1']}' and '{args['component2']}'"
        )

    # ---- placement matrices -------------------------------------------------
    # Position.GetComponents fills a CATSafeArrayVariant *out* parameter. That
    # does not marshal through late-bound IDispatch: the Python list passed in
    # comes back untouched, i.e. twelve zeros. Upstream then wrote those zeros
    # straight back with SetComponents — which erases the component's rotation —
    # and reported every component at (0, 0, 0). Same root cause as the GetCOG
    # bug fixed in 0.2.2, same fix: fill the array inside CATIA.
    _POSITION_SCRIPT = POSITION_SCRIPT

    def _position_components(self, position: Any) -> list[float]:
        return position_components(self.conn.app, position)

    @staticmethod
    def _resolve_component(product: Any, name: str) -> Any:
        """Accept a component name or its 1-based index (as a string)."""
        products = product.Products
        if name.isdigit():
            index = int(name)
            if 1 <= index <= products.Count:
                return products.Item(index)
        for i in range(1, products.Count + 1):
            component = products.Item(i)
            if component.Name == name:
                return component
        available = [products.Item(i).Name for i in range(1, products.Count + 1)]
        raise ValueError(f"No component '{name}'. Available: {available}")

    @staticmethod
    def _rotation_components(rows: list[list[float]],
                             convention: str = "matrix") -> list[float]:
        """Flatten `rotation_rows` into CATIA's nine placement numbers.

        CATIA keeps the placement as the three basis vectors: the first three
        numbers are where local X points in global, then Y, then Z. Those vectors
        are the *columns* of A in ``P_asm = A · P_local + T``.

        Two ways of writing the same 3x3 are in circulation, so the caller says
        which one it means:

        ``convention="matrix"`` (default, unchanged since 0.3.0)
            ``rows`` is A read row-major, i.e. the maths form
            ``P_asm = A · P_local + T``.
        ``convention="basis"``
            ``rows[i]`` is where local axis i points in global — the form CATIA
            prints, and the form a spec table gives when it says
            ``P_asm = Rᵀ · P_local + T`` with R's rows being those axes. The nine
            numbers then go in verbatim and ``get → set`` round-trips.
        """
        if convention == "basis":
            return [float(value) for row in rows for value in row]
        return [float(rows[r][c]) for c in range(3) for r in range(3)]

    @staticmethod
    def _matrix_report(components9: list[float]) -> dict[str, Any]:
        """Describe nine placement values in both conventions."""
        i_vec, j_vec, k_vec = components9[0:3], components9[3:6], components9[6:9]
        # rows = A (P_asm = A · P_local + T), whose columns are the three axes.
        rows = [[components9[0], components9[3], components9[6]],
                [components9[1], components9[4], components9[7]],
                [components9[2], components9[5], components9[8]]]
        det = (
            rows[0][0] * (rows[1][1] * rows[2][2] - rows[1][2] * rows[2][1])
            - rows[0][1] * (rows[1][0] * rows[2][2] - rows[1][2] * rows[2][0])
            + rows[0][2] * (rows[1][0] * rows[2][1] - rows[1][1] * rows[2][0])
        )
        ortho = max(
            abs(sum(rows[a][b] * rows[c][b] for b in range(3)) - (1.0 if a == c else 0.0))
            for a in range(3)
            for c in range(3)
        )
        return {
            "basis_columns": {
                "local_x_maps_to": [round(v, 9) for v in i_vec],
                "local_y_maps_to": [round(v, 9) for v in j_vec],
                "local_z_maps_to": [round(v, 9) for v in k_vec],
            },
            "basis_rows": [[round(v, 9) for v in vec] for vec in (i_vec, j_vec, k_vec)],
            "rotation_rows": [[round(v, 9) for v in row] for row in rows],
            "matrix_a": [[round(v, 9) for v in row] for row in rows],
            "checks": {
                "det": round(det, 9),
                "right_handed": abs(det - 1.0) < 1e-6,
                "orthogonality_error": round(ortho, 12),
                "orthogonal": ortho < 1e-6,
            },
        }

    def _get_component_transform(self, name: str) -> str:
        product = self.conn.get_active_product()
        component = self._resolve_component(product, name)
        values = self._position_components(component.Position)
        report = {
            "component": component.Name,
            "position_components": [round(v, 9) for v in values],
            "translation": [round(v, 6) for v in values[9:12]],
            **self._matrix_report(values[:9]),
            "note": (
                "position_components is CATIA's native order: the three basis vectors — "
                "where local X, then Y, then Z point in global — followed by the origin. "
                "`basis_rows` repeats them as a 3x3 and is what a spec table lists when it "
                "writes P_asm = Rᵀ·P_local + T; pass that back with "
                "rotation_convention='basis'. `rotation_rows` is the maths matrix A of "
                "P_asm = A·P_local + T (the default convention), i.e. basis_rowsᵀ."
            ),
        }
        return json.dumps(report, indent=2, ensure_ascii=False)

    def _set_component_transform(self, args: dict[str, Any]) -> str:
        product = self.conn.get_active_product()
        component = self._resolve_component(product, args["component_name"])

        rows = args.get("rotation_rows")
        components = args.get("position_components")
        translation = [float(v) for v in (args.get("translation") or [0.0, 0.0, 0.0])]
        if len(translation) != 3:
            raise ValueError("translation must hold exactly three numbers")

        if components is not None:
            values = [float(v) for v in components]
            if len(values) != 12:
                raise ValueError("position_components must hold exactly twelve numbers")
            values[9:12] = translation
            source = "position_components"
        elif rows is not None:
            if len(rows) != 3 or any(len(row) != 3 for row in rows):
                raise ValueError("rotation_rows must be a 3x3 array")
            convention = args.get("rotation_convention", "matrix")
            if convention not in ("matrix", "basis"):
                raise ValueError(
                    "rotation_convention must be 'matrix' (P_asm = A·P_local + T, the "
                    "default) or 'basis' (row i = where local axis i points in global)"
                )
            values = self._rotation_components(rows, convention) + translation
            source = (
                "rotation_rows, convention=basis (row i = where local axis i points "
                "in global)"
                if convention == "basis"
                else "rotation_rows, convention=matrix (row-major A with "
                     "P_asm = A·P_local + T)"
            )
        elif translation != [0.0, 0.0, 0.0]:
            # Translation-only move: keep the existing orientation.
            current = self._position_components(component.Position)
            values = current[:9] + translation
            source = "translation only (orientation preserved)"
        else:
            raise ValueError(
                "Provide rotation_rows, position_components, or a non-zero translation"
            )

        component.Position.SetComponents(values)
        self.conn.refresh_display()

        report: dict[str, Any] = {
            "component": component.Name,
            "source": source,
            "requested": {
                "position_components": [round(v, 9) for v in values],
                "translation": [round(v, 6) for v in translation],
            },
        }
        if args.get("verify", True):
            actual = self._position_components(component.Position)
            deviation = max(abs(a - b) for a, b in zip(actual, values))
            report["verified"] = {
                "position_components": [round(v, 9) for v in actual],
                "max_deviation": round(deviation, 12),
                "matches_request": deviation < 1e-6,
                **self._matrix_report(actual[:9]),
            }
            if not report["verified"]["checks"]["right_handed"]:
                report["warning"] = (
                    "det R != +1: the matrix mirrors the part. Check for a transposed or "
                    "sign-flipped row before trusting this placement."
                )
            elif deviation >= 1e-6:
                report["warning"] = (
                    "CATIA did not store the requested placement verbatim; inspect "
                    "verified.position_components before continuing."
                )
        return json.dumps(report, indent=2, ensure_ascii=False)

    def _move_component(self, args: dict[str, Any]) -> str:
        import math
        product = self.conn.get_active_product()
        component = self._resolve_component(product, args["component_name"])

        # Read through SystemService.Evaluate: a plain Python list would come back
        # as twelve zeros and writing it back would erase the rotation.
        position = component.Position
        matrix = self._position_components(position)

        # Apply translation (values 9, 10, 11 are tx, ty, tz)
        matrix[9] += args.get("tx", 0)
        matrix[10] += args.get("ty", 0)
        matrix[11] += args.get("tz", 0)

        # Apply rotations if specified (simplified: sequential Euler rotations)
        rx = math.radians(args.get("rx", 0))
        ry = math.radians(args.get("ry", 0))
        rz = math.radians(args.get("rz", 0))

        if rx != 0 or ry != 0 or rz != 0:
            # Build rotation matrix (Rz * Ry * Rx convention)
            cx, sx = math.cos(rx), math.sin(rx)
            cy, sy = math.cos(ry), math.sin(ry)
            cz, sz = math.cos(rz), math.sin(rz)

            # Rotation matrix components
            rot = [
                [cy * cz, cz * sx * sy - cx * sz, sx * sz + cx * cz * sy],
                [cy * sz, cx * cz + sx * sy * sz, cx * sy * sz - cz * sx],
                [-sy, cy * sx, cx * cy],
            ]

            # Apply to the current orientation. In CATIA's layout index 3*j + i is
            # A[i][j], so column j of the new matrix is rot · (column j of the old).
            old = matrix[:9]
            for j in range(3):
                for i in range(3):
                    matrix[3 * j + i] = sum(rot[i][k] * old[3 * j + k] for k in range(3))

        position.SetComponents(matrix)
        self.conn.refresh_display()

        return (
            f"Component '{args['component_name']}' moved: "
            f"T=({args.get('tx', 0)}, {args.get('ty', 0)}, {args.get('tz', 0)}) mm, "
            f"R=({args.get('rx', 0)}, {args.get('ry', 0)}, {args.get('rz', 0)})°"
        )

    def _list_components(self) -> str:
        """Every component with its placement, surviving broken links.

        One dead link used to abort the whole listing (reported 2026-10-24:
        "方法 PartNumber 失败" on a component whose part had been rebuilt), so a
        bad instance made the assembly impossible to diagnose AND impossible to
        fix. Each component is now read in its own try/except and reported as
        broken with the reason, and the rotation is published next to the
        translation - a placement is not a translation.
        """
        product = self.conn.get_active_product()
        products = product.Products

        components = []
        broken = 0
        for i in range(1, products.Count + 1):
            comp = products.Item(i)
            entry: dict[str, Any] = {"index": i}
            problems = []
            for key, getter in (("name", lambda c: str(c.Name)),
                                ("part_number", lambda c: str(c.PartNumber))):
                try:
                    entry[key] = getter(comp)
                except Exception as exc:  # noqa: BLE001 - reported per field
                    entry[key] = None
                    problems.append(f"{key}: {str(exc).splitlines()[0][:80]}")
            try:
                matrix = self._position_components(comp.Position)
                entry["position"] = {
                    "x": round(matrix[9], 3),
                    "y": round(matrix[10], 3),
                    "z": round(matrix[11], 3),
                }
                # A = the three axis images: columns 0/3/6, 1/4/7, 2/5/8.
                entry["rotation"] = [
                    [round(matrix[0], 6), round(matrix[3], 6), round(matrix[6], 6)],
                    [round(matrix[1], 6), round(matrix[4], 6), round(matrix[7], 6)],
                    [round(matrix[2], 6), round(matrix[5], 6), round(matrix[8], 6)],
                ]
            except Exception as exc:  # noqa: BLE001
                entry["position"] = None
                entry["rotation"] = None
                problems.append(f"position: {str(exc).splitlines()[0][:80]}")
            try:
                ref = comp.ReferenceProduct
                _ = ref.Parent
            except Exception as exc:  # noqa: BLE001 - the classic dead link
                problems.append(f"reference: {str(exc).splitlines()[0][:80]}")
            if problems:
                entry["broken"] = True
                entry["error"] = "; ".join(problems)
                broken += 1
            components.append(entry)

        if not components:
            return "No components in the active assembly"
        if broken:
            return json.dumps({
                "components": components,
                "count": len(components),
                "broken": broken,
                "note": ("Components marked broken cannot resolve their part "
                         "(the file was moved, or rebuilt and re-referenced). "
                         "Replace them with catia_add_component, or re-create the "
                         "assembly - the good components listed here are intact."),
            }, indent=2, ensure_ascii=False)
        return json.dumps(components, indent=2, ensure_ascii=False)

    def _list_constraints(self) -> str:
        product = self.conn.get_active_product()
        constraints = product.Connections("CATIAConstraints")

        cst_list = []
        for i in range(1, constraints.Count + 1):
            cst = constraints.Item(i)
            info = {
                "index": i,
                "name": cst.Name,
                "type": cst.Type if hasattr(cst, "Type") else "unknown",
            }
            try:
                info["status"] = "resolved" if cst.Status == 0 else "broken"
            except Exception:
                pass
            cst_list.append(info)

        if not cst_list:
            return "No constraints in the active assembly"
        return json.dumps(cst_list, indent=2, ensure_ascii=False)
