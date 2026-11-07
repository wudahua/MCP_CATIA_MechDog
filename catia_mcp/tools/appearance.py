"""Colour: per instance in an assembly, per tree node in a part.

This module exists because a picture is the only quick way to check an assembly
built from a text description, and until 0.3.79 nothing here could colour
anything: ``catia_set_visibility`` drove ``Selection.VisProperties`` for the SHOW
flag and left the other half of the interface alone, so every part came out the
same CATIA grey and "which of these 70 instances is the frame" had to be answered
in the tree.

The four measured facts those tools rest on - the four-argument
``SetRealColor``, the RGB sitting in the last three slots of ``GetRealColor``'s
return, the uncoloured default not being white, and the Part node not being a
colour target - are documented in :mod:`catia_mcp.colours`, which is also what
``catia_list_components`` uses. They live there ONCE on purpose.

What this module decides, and what it does not:

* ``catia_colorize_assembly`` colours by PART by default (instances of one part
  share a colour, which is what "colour by part" means) and by instance on
  request. It never wraps the palette around: two parts of one colour is the one
  outcome this tool must not produce, so colours past the palette are generated.
* ``catia_set_color`` sets one target. In an assembly that is an **instance**
  colour (stored in the CATProduct, the .CATPart untouched); in a part it is a
  **part** colour (stored in the CATPart, inherited by every feature inside the
  body). The reply states which, because the difference is the whole reason the
  two exist.
* ``catia_get_color`` reports what is DISPLAYED. It says when a value matches the
  uncoloured default, and never claims that nobody set it.
"""
from __future__ import annotations

from typing import Any

from catia_mcp.colours import (
    PALETTE,
    UNCOLOURED,
    apply_colour,
    generated_colour,
    hex_of,
    parse_colour,
    read_rgb,
    uncoloured_note,
)
from catia_mcp.connection import CATIAConnection
from catia_mcp.results import ArgumentRejected, PreconditionRejected
from catia_mcp.tools.assembly import AssemblyTools
from catia_mcp.tools.part_design import PartDesignTools

MAX_LINES = 40


class AppearanceTools:
    """Colour tools: an assembly's instances, a part's tree nodes."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection

    # ── registration ──────────────────────────────────────────────────────
    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_colorize_assembly",
                "description": (
                    "Give each DISTINCT part in the active assembly its own colour, "
                    "so a screenshot shows what the tree only spells out. One colour "
                    "per part number by default: the instances of one part share a "
                    "colour, which is what 'colour by part' means. 'per_instance=true' "
                    "gives every instance its own colour instead (useful to tell "
                    "identical instances apart). The colours are INSTANCE colours: "
                    "they live in the CATProduct, the .CATPart files are not touched, "
                    "and they survive save + reopen (measured on V5-6R2020 B30). Every "
                    "colour is read back and any mismatch is reported. Colours never "
                    "wrap around: past the palette they are generated, so two parts "
                    "cannot end up the same colour. The active document must be a "
                    "Product - a part uses catia_set_color. Returns the mapping."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "per_instance": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Colour each instance separately instead of each part. "
                                "Default false: instances of the same part share one "
                                "colour."
                            ),
                        },
                        "palette": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Hex colours in tree order, e.g. "
                                "['#FF0000', '#00A0FF']. Default: a 12-colour palette "
                                "chosen for contrast on a shaded solid. If there are "
                                "more parts than entries, the rest are generated "
                                "(distinct hues) and the reply says how many."
                            ),
                        },
                        "part_numbers": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Only colour these part numbers (or instance names). "
                                "Omitted = every component at the top level."
                            ),
                        },
                    },
                },
            },
            {
                "name": "catia_set_color",
                "description": (
                    "Set the colour of one component in an assembly, or of one tree "
                    "node / the whole solid in a part.\n"
                    "In a PRODUCT: 'target' is a component - its name, its 1-based "
                    "index, or {\"part_number\": \"P01_motor\", \"occurrence\": 2}. The "
                    "colour is set on that INSTANCE and lives in the CATProduct, so "
                    "the other instances and the .CATPart are unchanged; "
                    "'all_instances=true' colours every instance of the same part.\n"
                    "In a PART: 'target' is a tree node name (body, feature, sketch, "
                    "geometrical set - the same names catia_set_visibility takes); "
                    "omitted means the solid itself. Stored in the CATPart, and every "
                    "feature inside the body inherits it. A geometrical set colours "
                    "its contents too, because a set's colour does not reach them "
                    "(each element carries its own).\n"
                    "The colour is read back and the reply reports the measured value. "
                    "Nothing is saved: call catia_save_document."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": ["string", "object"],
                            "description": (
                                "Product: a component name, its 1-based index as a "
                                "string, or {\"part_number\": ..., \"occurrence\": n}. "
                                "Part: a tree node name. Omit in a part to colour the "
                                "whole solid (every body)."
                            ),
                        },
                        "color": {
                            "type": ["string", "array"],
                            "description": (
                                "'#RRGGBB', a colour name (red, green, grey, ...), "
                                "[r, g, b], or \"r,g,b\". The refusal lists the names."
                            ),
                        },
                        "all_instances": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Product only: colour every instance of the same part, "
                                "not just the one named. Ignored in a part document."
                            ),
                        },
                    },
                    "required": ["color"],
                },
            },
            {
                "name": "catia_get_color",
                "description": (
                    "Read back the colour of the components in the active assembly, or "
                    "of the solid / one tree node in the active part. Answers hex and "
                    "RGB per target, and flags a value that matches this build's "
                    "uncoloured default (measured (210, 210, 255) on B30) - which is "
                    "evidence, not proof: the interface reports the DISPLAYED colour, "
                    "so a part someone set to that exact value reads the same way. Use "
                    "it to check what catia_colorize_assembly did, or to find the one "
                    "instance whose colour did not take."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": ["string", "object"],
                            "description": (
                                "Product: a component name/index/{part_number, "
                                "occurrence}. Part: a tree node name. Omit to read all "
                                "components (product) or every body (part)."
                            ),
                        },
                    },
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        match tool_name:
            case "catia_colorize_assembly":
                return self._colorize_assembly(arguments)
            case "catia_set_color":
                return self._set_color(arguments)
            case "catia_get_color":
                return self._get_color(arguments)
            case _:
                raise ArgumentRejected(f"Unknown appearance tool: {tool_name}")

    # ── plumbing ──────────────────────────────────────────────────────────
    def _tree(self) -> tuple[str, Any]:
        """``("part", Part)`` or ``("product", Product)`` for the active document."""
        doc = self.conn.active_document
        try:
            return "part", self.conn.part_of(doc)
        except Exception:  # noqa: BLE001 - not a part; a product is the other case
            pass
        try:
            return "product", self.conn.product_of(doc)
        except Exception as error:  # noqa: BLE001
            raise PreconditionRejected(
                f"'{getattr(doc, 'Name', '?')}' is neither a Part nor a Product, so it "
                "has no tree to colour. Open a part or an assembly first (the call "
                "changed nothing)."
            ) from error

    @staticmethod
    def _mismatch(rgb: tuple[int, int, int],
                  back: tuple[int, int, int] | None) -> str:
        if back is None:
            return ", but the read-back failed"
        if back != rgb:
            return (f", but it reads back {hex_of(back)} = {back} - CATIA did not take "
                    "the value")
        return f", read back {hex_of(back)}"

    @staticmethod
    def _part_key(component: Any) -> str:
        """The part a component instantiates: part number, else the name's stem."""
        try:
            number = str(component.PartNumber)
        except Exception:  # noqa: BLE001 - a dead link still matches by name
            number = ""
        if number:
            return number.casefold()
        return str(component.Name).rsplit(".", 1)[0].casefold()

    def _siblings(self, product: Any, component: Any) -> list[str]:
        wanted = self._part_key(component)
        found = []
        try:
            count = int(product.Products.Count)
        except Exception:  # noqa: BLE001
            return found
        for index in range(1, count + 1):
            other = product.Products.Item(index)
            if str(other.Name) == str(component.Name):
                continue
            if self._part_key(other) == wanted:
                found.append(str(other.Name))
        return found

    # ── catia_set_color ───────────────────────────────────────────────────
    def _set_color(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        rgb = parse_colour(args.get("color"))
        target = args.get("target")
        all_instances = bool(args.get("all_instances", False))
        kind, tree = self._tree()
        doc = self.conn.active_document
        doc_name = str(getattr(doc, "Name", "?"))

        if kind == "product":
            return self._set_component_colour(tree, doc, doc_name, target, rgb,
                                              all_instances)
        note = ""
        if all_instances:
            note = (" 'all_instances' means nothing in a part document and was "
                    "ignored.")
        return self._set_part_colour(tree, doc, doc_name, target, rgb) + note

    def _set_component_colour(self, product: Any, doc: Any, doc_name: str,
                              target: Any, rgb: tuple[int, int, int],
                              all_instances: bool) -> str:
        if target is None:
            available = [str(product.Products.Item(i).Name)
                         for i in range(1, int(product.Products.Count) + 1)]
            raise ArgumentRejected(
                "A product document needs 'target' - which component to colour. "
                f"Available: {available or '[] (the assembly is empty)'}. To colour "
                "every part at once use catia_colorize_assembly."
            )
        component = AssemblyTools._resolve_component(product, target)
        selection = doc.Selection

        painted: list[str] = []
        try:
            if all_instances:
                wanted = self._part_key(component)
                for index in range(1, int(product.Products.Count) + 1):
                    other = product.Products.Item(index)
                    if self._part_key(other) == wanted:
                        back = apply_colour(selection, other, rgb)
                        painted.append(f"{other.Name}{self._mismatch(rgb, back)}")
            else:
                back = apply_colour(selection, component, rgb)
                painted.append(f"{component.Name}{self._mismatch(rgb, back)}")
        finally:
            try:
                selection.Clear()
            except Exception:  # noqa: BLE001 - a selection left behind
                pass

        head = (f"Colour {hex_of(rgb)} = {rgb} set on "
                + ("every instance of " if all_instances else "")
                + f"'{component.Name}' in '{doc_name}'.")
        siblings = [] if all_instances else self._siblings(product, component)
        scope = (
            " This is an INSTANCE colour: it lives in the CATProduct, so the .CATPart "
            "file is unchanged"
            + ("" if not siblings
               else f" and the other {len(siblings)} instance(s) of the same part "
                    f"({', '.join(siblings)}) keep their own")
            + "."
        )
        return (head + scope + "\n  " + "\n  ".join(painted)
                + "\nNot saved yet: call catia_save_document.")

    def _set_part_colour(self, part: Any, doc: Any, doc_name: str,
                         target: Any, rgb: tuple[int, int, int]) -> str:
        selection = doc.Selection
        if target is None:
            bodies = [part.Bodies.Item(index)
                      for index in range(1, int(part.Bodies.Count) + 1)]
            if not bodies:
                raise PreconditionRejected(
                    f"'{doc_name}' holds no body, so there is no solid to colour. "
                    "Build a feature first (the call changed nothing)."
                )
            lines = []
            try:
                for body in bodies:
                    back = apply_colour(selection, body, rgb)
                    lines.append(f"'{body.Name}'{self._mismatch(rgb, back)}")
            finally:
                try:
                    selection.Clear()
                except Exception:  # noqa: BLE001
                    pass
            return (f"Colour {hex_of(rgb)} = {rgb} set on {len(bodies)} body(ies) of "
                    f"'{doc_name}', the whole solid.\n  " + "\n  ".join(lines)
                    + "\nNot saved yet: call catia_save_document.")

        name = str(target)
        node, where, parent_set = PartDesignTools._find_tree_node(part, name)
        if node is None:
            raise ArgumentRejected(
                f"No tree node named '{name}' in '{doc_name}'. Use catia_list_bodies, "
                "catia_list_features or catia_gsd_list_elements to see the names."
            )
        # EVERY node with this name, not the one Item() returned: a part can hold
        # several (P04 of the 2026-11-06 build holds four 'Datum_frame'), and
        # colouring one while the reply says "coloured" is the defect this server
        # withdrew for hiding in 0.3.74.
        nodes = PartDesignTools._nodes_named(part, name) or [node]
        lines = []
        contents = 0
        try:
            for each in nodes:
                back = apply_colour(selection, each, rgb)
                lines.append(f"'{name}'{self._mismatch(rgb, back)}")
            # A set's colour does not reach its contents - each element carries
            # its own, exactly like visibility. Descend (bounded), so "the set is
            # grey" is true of what is inside it too.
            if where in ("geometrical set", "hybrid body"):
                contents = PartDesignTools._hide_set_contents(
                    node, lambda child: apply_colour(selection, child, rgb), depth=0)
        finally:
            try:
                selection.Clear()
            except Exception:  # noqa: BLE001
                pass

        extra = f" and {contents} element(s) inside it" if contents else ""
        if len(nodes) > 1:
            extra += f" (and {len(nodes) - 1} other node(s) carrying that name)"
        where_text = where + (f" in '{parent_set}'" if parent_set else "")
        return (f"Colour {hex_of(rgb)} = {rgb} set on '{name}' ({where_text}) in "
                f"'{doc_name}'{extra}.\n  " + "\n  ".join(lines)
                + "\nNot saved yet: call catia_save_document.")

    # ── catia_colorize_assembly ───────────────────────────────────────────
    def _colorize_assembly(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        per_instance = bool(args.get("per_instance", False))
        raw_palette = list(args.get("palette") or PALETTE)
        wanted = [str(name).strip().casefold()
                  for name in (args.get("part_numbers") or []) if str(name).strip()]

        kind, product = self._tree()
        doc = self.conn.active_document
        doc_name = str(getattr(doc, "Name", "?"))
        if kind != "product":
            raise PreconditionRejected(
                f"The active document '{doc_name}' is a Part, not an assembly, so "
                "there are no components to colour by part. Use catia_set_color for a "
                "part (the call changed nothing)."
            )
        products = product.Products
        count = int(products.Count)
        if count == 0:
            raise PreconditionRejected(
                f"'{doc_name}' holds no component, so there is nothing to colour. "
                "Insert one first (catia_add_component) - nothing was changed."
            )

        components = [products.Item(index) for index in range(1, count + 1)]
        if wanted:
            components = [component for component in components
                          if self._part_key(component) in wanted
                          or str(component.Name).casefold() in wanted]
            if not components:
                available = [str(products.Item(i).Name) for i in range(1, count + 1)]
                raise ArgumentRejected(
                    "None of the requested part number(s) "
                    f"{args.get('part_numbers')} is in '{doc_name}'. "
                    f"Available: {available}"
                )

        # Group in tree order: one group per part, or one per instance.
        groups: dict[str, list[Any]] = {}
        for component in components:
            key = str(component.Name) if per_instance else self._part_key(component)
            groups.setdefault(key, []).append(component)

        colours: list[tuple[int, int, int]] = []
        for index in range(len(groups)):
            colours.append(parse_colour(raw_palette[index]) if index < len(raw_palette)
                           else generated_colour(index))

        selection = doc.Selection
        lines: list[str] = []
        failures: list[str] = []
        already = 0
        try:
            for (key, members), rgb in zip(groups.items(), colours):
                first = members[0]
                selection.Clear()
                selection.Add(first)
                before = read_rgb(selection)
                if before is not None and before != UNCOLOURED:
                    # A re-run silently overwriting a caller's own scheme is worth
                    # one sentence.
                    already += 1
                for component in members:
                    back = apply_colour(selection, component, rgb)
                    if back != rgb:
                        failures.append(
                            f"{component.Name} -> "
                            f"{hex_of(back) if back is not None else 'unreadable'}")
                label = str(first.Name) if per_instance else self._part_key(first)
                names = ", ".join(str(member.Name) for member in members)
                lines.append("  " + hex_of(rgb) + "  " + label
                             + (f"  ({len(members)} instance(s): {names})"
                                if len(members) > 1 else ""))
        finally:
            try:
                selection.Clear()
            except Exception:  # noqa: BLE001
                pass

        shown = "\n".join(lines[:MAX_LINES])
        tail = (f"\n  ... {len(lines) - MAX_LINES} more"
                if len(lines) > MAX_LINES else "")
        instance_total = sum(len(members) for members in groups.values())
        head = (f"{len(groups)} colour(s) over {instance_total} instance(s) in "
                f"'{doc_name}'"
                + (" - one per INSTANCE" if per_instance else " - one per part")
                + f" (palette of {len(raw_palette)}"
                + (f", {len(groups) - len(raw_palette)} generated"
                   if len(groups) > len(raw_palette) else "")
                + "):")
        notes = []
        if already:
            notes.append(f"{already} already carried a colour, which was overwritten")
        if failures:
            notes.append("did NOT take: " + "; ".join(failures[:8])
                         + (f" and {len(failures) - 8} more"
                            if len(failures) > 8 else ""))
        else:
            notes.append("every colour read back exactly")
        return (head + "\n" + shown + tail + "\n" + "; ".join(notes) + "."
                + " Instance colours live in the CATProduct (the .CATPart files are "
                "untouched) and survive save + reopen. Not saved yet: call "
                "catia_save_document.")

    # ── catia_get_color ───────────────────────────────────────────────────
    def _get_color(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        target = args.get("target")
        kind, tree = self._tree()
        doc = self.conn.active_document
        doc_name = str(getattr(doc, "Name", "?"))
        selection = doc.Selection

        if kind == "product":
            if target is not None:
                component = AssemblyTools._resolve_component(tree, target)
                back = self._read_one(selection, component)
                return (f"'{component.Name}' in '{doc_name}': {self._describe(back)}"
                        + uncoloured_note(back))
            lines = []
            count = int(tree.Products.Count)
            for index in range(1, count + 1):
                component = tree.Products.Item(index)
                back = self._read_one(selection, component)
                lines.append(f"  {component.Name}: {self._describe(back)}"
                             + uncoloured_note(back))
            if not lines:
                return f"'{doc_name}' holds no component."
            shown = "\n".join(lines[:MAX_LINES])
            tail = (f"\n  ... {len(lines) - MAX_LINES} more"
                    if len(lines) > MAX_LINES else "")
            return f"{len(lines)} component(s) in '{doc_name}':\n" + shown + tail

        if target is not None:
            name = str(target)
            node, where, parent_set = PartDesignTools._find_tree_node(tree, name)
            if node is None:
                raise ArgumentRejected(
                    f"No tree node named '{name}' in '{doc_name}'. Use "
                    "catia_list_bodies, catia_list_features or "
                    "catia_gsd_list_elements to see the names."
                )
            back = self._read_one(selection, node)
            where_text = where + (f" in '{parent_set}'" if parent_set else "")
            return (f"'{name}' ({where_text}) in '{doc_name}': {self._describe(back)}"
                    + uncoloured_note(back))

        lines = []
        for index in range(1, int(tree.Bodies.Count) + 1):
            body = tree.Bodies.Item(index)
            back = self._read_one(selection, body)
            lines.append(f"  '{body.Name}': {self._describe(back)}"
                         + uncoloured_note(back))
        if not lines:
            return f"'{doc_name}' holds no body."
        return f"{len(lines)} body(ies) in '{doc_name}':\n" + "\n".join(lines)

    @staticmethod
    def _read_one(selection: Any, node: Any) -> tuple[int, int, int] | None:
        selection.Clear()
        selection.Add(node)
        value = read_rgb(selection)
        selection.Clear()
        return value

    @staticmethod
    def _describe(rgb: tuple[int, int, int] | None) -> str:
        if rgb is None:
            return "unreadable (VisProperties gave no colour)"
        return f"{hex_of(rgb)} = {rgb}"
