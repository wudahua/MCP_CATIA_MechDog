"""Assembly tools for CATIA V5.

Product/Assembly management: add components, constraints (Fix, Coincidence,
Contact, Offset, Angle), move components, and manage the product tree.
"""

from __future__ import annotations

import json
from typing import Any

from catia_mcp.connection import CATIAConnection


class AssemblyTools:
    """Tools for assembly (Product) operations in CATIA V5."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_add_component",
                "description": (
                    "Add an existing CATPart or CATProduct file as a component in the active assembly. "
                    "The component is inserted at the origin."
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
                    "translation. Accepts `rotation_rows` (3x3 row-major, the way specs and "
                    "papers write P_asm = R * P_local + T — the server transposes it for you) "
                    "or `position_components` (12 numbers in CATIA's native order: three "
                    "basis vectors then the origin). Reads the placement back and reports the "
                    "deviation, det and orthogonality, so a wrong handedness or a stray "
                    "transpose cannot pass silently."
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
                            "description": "3x3 rotation, ROW-major: [[R00,R01,R02],[R10,...],[...]]",
                            "items": {"type": "array", "items": {"type": "number"}},
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
        product = self.conn.get_active_product()
        products = product.Products
        component = products.AddComponentsFromFiles(
            [file_path], "All"
        )
        self.conn.refresh_display()
        return f"Component added from: {file_path}"

    def _add_new_part(self, name: str | None = None) -> str:
        product = self.conn.get_active_product()
        products = product.Products
        new_product = products.AddNewProduct("Part")
        if name:
            new_product.Name = name
        self.conn.refresh_display()
        return f"New Part component created in assembly: '{new_product.Name}'"

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

    def _fix_constraint(self, component_name: str) -> str:
        product = self.conn.get_active_product()
        constraints = product.Connections("CATIAConstraints")

        component = product.Products.Item(component_name)
        cst = constraints.AddMonoEltCst(0, component)  # Fix constraint
        cst.Name = f"Fix.{component_name}"

        self.conn.refresh_display()
        return f"Fix constraint applied to '{component_name}'"

    def _coincidence_constraint(self, args: dict[str, Any]) -> str:
        product = self.conn.get_active_product()
        constraints = product.Connections("CATIAConstraints")

        comp1 = product.Products.Item(args["component1"])
        comp2 = product.Products.Item(args["component2"])

        cst = constraints.AddBiEltCst(0, comp1, comp2)  # Coincidence
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

        cst = constraints.AddBiEltCst(1, comp1, comp2)  # Offset
        cst.Dimension.Value = args["offset"]

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

        cst = constraints.AddBiEltCst(2, comp1, comp2)  # Angle
        cst.Dimension.Value = args["angle"]

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
    _POSITION_SCRIPT = (
        "Public Function position_components(p)\n"
        "    Dim a(11)\n"
        "    p.GetComponents a\n"
        "    position_components = a\n"
        "End Function\n"
    )

    def _position_components(self, position: Any) -> list[float]:
        raw = self.conn.app.SystemService.Evaluate(
            self._POSITION_SCRIPT, 0, "position_components", [position]
        )
        return [float(value) for value in raw]

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
    def _matrix_report(components9: list[float]) -> dict[str, Any]:
        """Describe nine rotation values read in CATIA's own (column) order."""
        i_vec, j_vec, k_vec = components9[0:3], components9[3:6], components9[6:9]
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
            "rotation_rows": [[round(v, 9) for v in row] for row in rows],
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
                "position_components is CATIA's native order: the three basis vectors "
                "(columns of R) then the origin. rotation_rows is the same matrix written "
                "row-major, i.e. the form P_asm = R * P_local + T assumes."
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
            # R is given row-major; CATIA wants the columns, so flatten per column.
            values = [float(rows[r][c]) for c in range(3) for r in range(3)] + translation
            source = "rotation_rows (transposed into CATIA's column order)"
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
            r00 = cy * cz
            r01 = cz * sx * sy - cx * sz
            r02 = sx * sz + cx * cz * sy
            r10 = cy * sz
            r11 = cx * cz + sx * sy * sz
            r12 = cx * sy * sz - cz * sx
            r20 = -sy
            r21 = cy * sx
            r22 = cx * cy

            # Apply to current rotation (first 9 elements)
            old = matrix[:9]
            matrix[0] = r00 * old[0] + r01 * old[3] + r02 * old[6]
            matrix[1] = r00 * old[1] + r01 * old[4] + r02 * old[7]
            matrix[2] = r00 * old[2] + r01 * old[5] + r02 * old[8]
            matrix[3] = r10 * old[0] + r11 * old[3] + r12 * old[6]
            matrix[4] = r10 * old[1] + r11 * old[4] + r12 * old[7]
            matrix[5] = r10 * old[2] + r11 * old[5] + r12 * old[8]
            matrix[6] = r20 * old[0] + r21 * old[3] + r22 * old[6]
            matrix[7] = r20 * old[1] + r21 * old[4] + r22 * old[7]
            matrix[8] = r20 * old[2] + r21 * old[5] + r22 * old[8]

        position.SetComponents(matrix)
        self.conn.refresh_display()

        return (
            f"Component '{args['component_name']}' moved: "
            f"T=({args.get('tx', 0)}, {args.get('ty', 0)}, {args.get('tz', 0)}) mm, "
            f"R=({args.get('rx', 0)}, {args.get('ry', 0)}, {args.get('rz', 0)})°"
        )

    def _list_components(self) -> str:
        product = self.conn.get_active_product()
        products = product.Products

        components = []
        for i in range(1, products.Count + 1):
            comp = products.Item(i)
            pos = comp.Position
            try:
                matrix = self._position_components(pos)
            except Exception:
                matrix = [0.0] * 12
            components.append({
                "index": i,
                "name": comp.Name,
                "part_number": comp.PartNumber,
                "position": {
                    "x": round(matrix[9], 3),
                    "y": round(matrix[10], 3),
                    "z": round(matrix[11], 3),
                },
            })

        if not components:
            return "No components in the active assembly"
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
