"""Knowledgeware (CATIA "知识工程") tools for CATIA V5 - ported from upstream.

Parameters, formulas, design tables and relation listing on the active Part,
through the documented ``Parameters`` / ``Relations`` automation interfaces.
Lengths and angles are valued from strings with explicit units ("20mm",
"45deg"), which sidesteps the magnitude/unit ambiguity of ``Dimension.Value``.

Provenance: this module was dropped when the fork was cut and brought back in
0.3.63. It is NOT a copy - every attribute name was exercised on B30 first, and
four of them did not behave the way upstream's code assumes:

* ``DesignTable`` has no ``ConfigurationName``, ``Configurations`` or
  ``NumberOfConfigurations``. The interface declares ``Configuration`` (readable
  and writable), ``ConfigurationsNb``, ``ColumnsNb``, ``Activate``,
  ``Deactivate``, ``Synchronize``. So upstream could create a table it had no way
  to use; ``catia_apply_design_table`` here switches one.
* A design table's length column is read in METRES when the cell has no unit:
  a row reading ``30`` set a length parameter to 30000 mm. ``30mm`` and ``0.03m``
  both give 30. Documented in the tool description because it is a 1000x part.
* A parameter associated with a design table cannot be re-valued directly any
  more - ``ValuateFromString`` fails with a bare COM error. The tools here say
  so and point at ``catia_apply_design_table``.
* ``bool("false")`` is True in Python, so the boolean parameter type would have
  silently inverted a false. Parsed properly here.

What upstream got right, and is kept: a relation's ``Value`` IS its expression
(CATIA rewrites it to full parameter paths), ``AddAssociation(parameter, column)``
takes them in that order, and lengths really do need ``ValuateFromString`` rather
than a bare ``Value`` assignment.
"""

from __future__ import annotations

import json
import os
from typing import Any

from catia_mcp.connection import CATIAConnection
from catia_mcp.results import ArgumentRejected, FeatureRejected

_UNITS = {"length": "mm", "angle": "deg"}

# What a design table does to an un-united length cell. Measured on B30
# 2026-11-05: a sheet reading "30" set H to 30000 mm, "30mm" and "0.03m" both
# gave 30. A product family built from a plain Excel column would come out a
# thousand times too large and nothing in the reply would say why.
_TABLE_UNIT_NOTE = (
    "Design-table cells for a LENGTH or ANGLE parameter must carry their unit "
    "('30mm', '45deg'): a bare number in a length column is read as METRES "
    "(measured on B30: '30' set a 20 mm parameter to 30000 mm, while '30mm' and "
    "'0.03m' both gave 30)."
)

_TRUTHY = {"true", "1", "yes", "y", "on"}
_FALSY = {"false", "0", "no", "n", "off"}


def _as_bool(value: Any) -> bool:
    """A boolean from anything a caller may send, including the string "false".

    ``bool("false")`` is True, so feeding the raw value to CATIA inverted every
    false that arrived as text. Unknown text is refused rather than guessed:
    a parameter that says True because someone typed "nope" is a wrong model,
    not a default.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in _TRUTHY:
        return True
    if text in _FALSY:
        return False
    raise ValueError(
        f"{value!r} is not a boolean. Use true/false (or 1/0, yes/no)."
    )


class KnowledgeTools:
    """Tools for CATIA V5 Knowledgeware (parameters, formulas, design tables)."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_create_parameter",
                "description": (
                    "Create a user parameter on the active Part, or set its "
                    "value if it already exists. Types: length (mm), angle "
                    "(deg), real, integer, string, boolean. Parameters can "
                    "then drive features through formulas. "
                    "Two measured behaviours: a parameter that a design table "
                    "is associated with can no longer be re-valued directly "
                    "(CATIA refuses) - switch the table's configuration with "
                    "catia_apply_design_table instead; and a boolean sent as "
                    "the STRING 'false' is parsed as false, where a naive "
                    "bool() would have made it true."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Parameter name (e.g. 'Thickness')"},
                        "type": {
                            "type": "string",
                            "enum": ["length", "angle", "real", "integer", "string", "boolean"],
                            "description": "Parameter type",
                        },
                        "value": {"description": "Initial value (number, string or boolean)"},
                    },
                    "required": ["name", "type", "value"],
                },
            },
            {
                "name": "catia_set_formula",
                "description": (
                    "Create or update a formula driving a parameter of the "
                    "active Part. The expression uses CATIA syntax, with "
                    "parameter paths in backticks, e.g. "
                    "\"`PartBody\\\\Pad.1\\\\FirstLimit\\\\Length` * 2\" or "
                    "\"Thickness + 5mm\". If a relation with the same name "
                    "exists it is modified in place. "
                    "TARGET_NAMES ARE LOCALIZED AND POSITIONAL: a feature's own "
                    "dimension is reachable, but only by the name CATIA reports, "
                    "and a Chinese build spells it '零件几何体\\凸台.1\\第一限制\\长度' "
                    "(measured on B30 - the English 'PartBody\\Pad.1\\FirstLimit"
                    "\\Length' is not found). Copy the exact name from "
                    "catia_get_parameters; the failure reply lists near matches."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Formula (relation) name"},
                        "target_parameter": {
                            "type": "string",
                            "description": "Parameter that receives the result (name or full path from catia_get_parameters)",
                        },
                        "expression": {"type": "string", "description": "Formula body in CATIA syntax"},
                        "comment": {"type": "string", "description": "Optional comment"},
                    },
                    "required": ["name", "target_parameter", "expression"],
                },
            },
            {
                "name": "catia_list_relations",
                "description": (
                    "List the relations of the active Part (formulas, design "
                    "tables, rules, checks) with their bodies and comments. "
                    "Optional substring filter."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "filter": {"type": "string", "description": "Case-insensitive substring filter"},
                    },
                },
            },
            {
                "name": "catia_delete_relation",
                "description": "Delete a relation (formula, design table, rule, check) of the active Part by name.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Relation name (see catia_list_relations)"},
                    },
                    "required": ["name"],
                },
            },
            {
                "name": "catia_create_design_table",
                "description": (
                    "Create a design table from an Excel or text sheet "
                    "(absolute path on the CATIA machine) and associate its "
                    "columns to parameters of the active Part by name. The "
                    "sheet's column headers must match the parameter names "
                    "given in 'parameters'. "
                    + _TABLE_UNIT_NOTE +
                    " Use catia_apply_design_table to switch the part to a row."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Design table name"},
                        "file_path": {"type": "string", "description": "Absolute path to the .xls/.xlsx/.txt sheet on the CATIA machine"},
                        "parameters": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Parameter names to associate (one per sheet column with the same header)",
                        },
                        "copy_mode": {
                            "type": "boolean",
                            "description": "Copy the sheet data into the part (default true) rather than linking the file",
                        },
                        "comment": {"type": "string", "description": "Optional comment"},
                    },
                    "required": ["name", "file_path", "parameters"],
                },
            },
            {
                "name": "catia_apply_design_table",
                "description": (
                    "Switch the active Part to one row of a design table, which "
                    "re-values every parameter the table is associated with. "
                    "Rows are numbered from 1 in sheet order. "
                    "WHY THIS EXISTS: upstream's design-table tool could create a "
                    "table but never apply one - it called the configuration "
                    "property 'ConfigurationName', and the DesignTable interface "
                    "has no such member. The real one is 'Configuration', it is "
                    "writable, and applying row 2 of a two-row sheet did change "
                    "the part (measured on B30 2026-11-05). Without this call a "
                    "table is a file association a human has to activate by hand."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Design table name (see catia_list_relations)"},
                        "configuration": {
                            "type": "integer",
                            "description": "Row number to apply, 1-based in sheet order",
                        },
                    },
                    "required": ["name", "configuration"],
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        match tool_name:
            case "catia_create_parameter":
                return self._create_parameter(arguments)
            case "catia_set_formula":
                return self._set_formula(arguments)
            case "catia_list_relations":
                return self._list_relations(arguments.get("filter"))
            case "catia_delete_relation":
                return self._delete_relation(arguments["name"])
            case "catia_create_design_table":
                return self._create_design_table(arguments)
            case "catia_apply_design_table":
                return self._apply_design_table(arguments)
            case _:
                raise ValueError(f"Unknown knowledge tool: {tool_name}")

    # ------------------------------------------------------------------
    def _find_parameter(self, params: Any, name: str) -> Any | None:
        try:
            return params.Item(name)
        except Exception:
            return None

    @staticmethod
    def _near_names(params: Any, wanted: str, limit: int = 6) -> list[str]:
        """Parameter names that end the way `wanted` ends.

        The lookup needs the exact name CATIA reports, and a Chinese build
        reports feature parameters in Chinese ("零件几何体\\凸台.1\\第一限制\\长度").
        Answering only "no such parameter" sends the caller looking for a bug in
        the server; two matching path segments are enough to point at the right
        entry.
        """
        tail = [part for part in str(wanted).replace("/", "\\").split("\\")
                if part and part not in ("PartBody", "Part1")]
        needle = [part.lower() for part in tail[-2:]]
        rows: list[tuple[int, str]] = []
        try:
            for index in range(1, params.Count + 1):
                name = str(params.Item(index).Name)
                lowered = name.lower()
                score = sum(1 for part in needle if part in lowered)
                if score:
                    rows.append((score, name))
        except Exception:  # noqa: BLE001 - a hint is not worth an error
            return []
        rows.sort(key=lambda row: (-row[0], len(row[1])))
        return [name for _score, name in rows[:limit]]

    def _create_parameter(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        params = part.Parameters
        name, ptype = args["name"], args["type"]
        value = args["value"]
        if ptype == "boolean":
            value = _as_bool(value)

        # Convert BEFORE anything is created. The old order was
        #   param = params.CreateDimension(name, "LENGTH", 0)
        #   param.ValuateFromString(f"{float(value)}mm")   <- raises here
        # so catia_create_parameter{value:"abc"} answered a failure and left a
        # value=0 parameter behind - a call that said it did not work and had
        # already changed the model (reported 2026-11-05, P2). Same for angle.
        coerced = self._coerce_value(ptype, value, name)
        if coerced is not None:
            value = coerced

        existing = self._find_parameter(params, name)
        if existing is not None:
            try:
                self._assign(existing, ptype, value)
            except Exception as error:  # noqa: BLE001
                raise RuntimeError(
                    f"Parameter '{name}' exists but its value could not be set to "
                    f"{value!r} ({str(error).splitlines()[0][:110]}). Two causes, "
                    "both measured on B30: the parameter is ASSOCIATED WITH A "
                    "DESIGN TABLE, which then owns its value - switch rows with "
                    "catia_apply_design_table instead of writing it; or the type "
                    "given here does not match the existing parameter (the type "
                    "is never changed by this tool, only the value)."
                ) from error
            part.Update()
            return (f"Parameter '{existing.Name}' already existed; value set to "
                    f"{value}{_UNITS.get(ptype, '')} (type unchanged).")

        created: Any = None
        try:
            if ptype == "length":
                created = params.CreateDimension(name, "LENGTH", 0)
                created.ValuateFromString(f"{float(value)}mm")
            elif ptype == "angle":
                created = params.CreateDimension(name, "ANGLE", 0)
                created.ValuateFromString(f"{float(value)}deg")
            elif ptype == "real":
                created = params.CreateReal(name, float(value))
            elif ptype == "integer":
                created = params.CreateInteger(name, int(value))
            elif ptype == "string":
                created = params.CreateString(name, str(value))
            elif ptype == "boolean":
                created = params.CreateBoolean(name, bool(value))
            else:
                raise ArgumentRejected(
                    f"Unknown parameter type '{ptype}'. Use one of: length, "
                    "angle, real, integer, string, boolean.",
                    next_actions=["Pass type='length' (mm), 'angle' (deg), "
                                  "'real', 'integer', 'string' or 'boolean' - "
                                  "nothing was created"],
                )
        except Exception as error:
            # A created-then-failed parameter is the 0.3.68 P2 defect: the call
            # must either not create it or take it back out. The value is
            # already converted above, so reaching here means CATIA itself
            # refused the assignment.
            if created is not None:
                residual = self._discard_parameter(params, created, name)
                if residual:
                    raise FeatureRejected(
                        f"{error}{residual}", rolled_back=False,
                        next_actions=[
                            f"Delete the leftover parameter '{name}' with "
                            "catia_list_relations / the Part tree before "
                            "retrying - it is already in the model",
                        ],
                    ) from error
                raise ArgumentRejected(
                    f"{error} The half-created parameter '{name}' was removed "
                    "again, so the model is unchanged.",
                    next_actions=["Fix the value and retry - the failed "
                                  "parameter was deleted, nothing is left over"],
                ) from error
            raise
        part.Update()
        return (f"Parameter '{created.Name}' ({ptype}) created with value "
                f"{value}{_UNITS.get(ptype, '')}.")

    @staticmethod
    def _discard_parameter(params: Any, param: Any, name: str) -> str:
        """Remove a parameter this call created; returns a note if it survived."""
        for target in (getattr(param, "Name", None), name):
            if not target:
                continue
            try:
                params.Remove(str(target))
                return ""
            except Exception:  # noqa: BLE001 - try the other spelling
                continue
        return (f" The parameter '{name}' could not be removed again, so it is "
                "still in the model with its old value.")

    @staticmethod
    def _coerce_value(ptype: str, value: Any, name: str) -> Any:
        """The numeric form of ``value``, refused before anything is created.

        length and angle are millimetres and degrees (the same units
        ``_UNITS`` writes back); a unit suffix such as ``'30mm'`` belongs in a
        design-table CELL, where a bare number means metres - not here
        (reported 2026-11-05, P2, and 0.3.63 for the table rule).
        """
        if ptype in ("length", "angle"):
            unit = "millimetres" if ptype == "length" else "degrees"
            suffix = "mm" if ptype == "length" else "deg"
            try:
                return float(value)
            except (TypeError, ValueError):
                raise ArgumentRejected(
                    f"Parameter '{name}' is a {ptype}, so its value is a plain "
                    f"number in {unit}; got {value!r}. Write 30 (or \"30\") for "
                    f"30 {suffix}. A unit suffix ('30mm') only applies inside a "
                    "design-table cell. Nothing was created.",
                    next_actions=[
                        f"Pass value as a number (30), not a string with units "
                        f"('30{suffix}') - no parameter was created",
                    ],
                ) from None
        if ptype == "real":
            try:
                return float(value)
            except (TypeError, ValueError):
                raise ArgumentRejected(
                    f"Parameter '{name}' is a real, so its value must be a "
                    f"number; got {value!r}. Nothing was created.",
                    next_actions=["Pass a number for value - nothing was "
                                  "created"],
                ) from None
        if ptype == "integer":
            try:
                return int(value)
            except (TypeError, ValueError):
                raise ArgumentRejected(
                    f"Parameter '{name}' is an integer, so its value must be a "
                    f"whole number; got {value!r}. Nothing was created.",
                    next_actions=["Pass a whole number for value - nothing was "
                                  "created"],
                ) from None
        return value

    @staticmethod
    def _assign(param: Any, ptype: str, value: Any) -> None:
        if ptype in _UNITS:
            param.ValuateFromString(f"{float(value)}{_UNITS[ptype]}")
        elif ptype == "integer":
            param.Value = int(value)
        elif ptype == "real":
            param.Value = float(value)
        elif ptype == "boolean":
            param.Value = _as_bool(value)
        else:
            param.Value = str(value)

    def _set_formula(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        params = part.Parameters
        wanted = args["target_parameter"]
        target = self._find_parameter(params, wanted)
        if target is None:
            near = self._near_names(params, wanted)
            hint = (" Names that end the same way: " + ", ".join(f"'{n}'" for n in near)
                    + "." if near else "")
            raise RuntimeError(
                f"No parameter '{wanted}' on the active part. The lookup is exact, "
                "and a feature's own dimension is named in the build's language - "
                "on this Chinese B30 a pad's height is "
                "'零件几何体\\凸台.1\\第一限制\\长度', not 'PartBody\\Pad.1\\FirstLimit"
                "\\Length' (measured 2026-11-05). Copy the exact name from "
                f"catia_get_parameters.{hint}"
            )
        relations = part.Relations
        name = args["name"]
        comment = args.get("comment") or ""
        expression = args["expression"]
        existing = None
        try:
            existing = relations.Item(name)
        except Exception:
            pass
        if existing is not None:
            existing.Modify(expression)
            part.Update()
            return f"Formula '{name}' updated: {expression}"
        formula = relations.CreateFormula(name, comment, target, expression)
        part.Update()
        return f"Formula '{formula.Name}' created on '{target.Name}': {expression}"

    def _list_relations(self, name_filter: str | None) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        relations = part.Relations
        needle = (name_filter or "").lower()
        rows = []
        for i in range(1, relations.Count + 1):
            rel = relations.Item(i)
            row: dict[str, Any] = {"index": i, "name": str(rel.Name)}
            for attr, key in (("Value", "body"), ("Comment", "comment")):
                try:
                    v = getattr(rel, attr)
                    if v not in (None, ""):
                        row[key] = str(v)
                except Exception:
                    pass
            if needle and needle not in json.dumps(row).lower():
                continue
            rows.append(row)
        if not rows:
            return "No relations" + (f" matching '{name_filter}'" if name_filter else "") + " on the active part."
        return json.dumps(rows, indent=2, ensure_ascii=False)

    def _delete_relation(self, name: str) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        relations = part.Relations
        try:
            relations.Item(name)
        except Exception:
            raise RuntimeError(f"No relation named '{name}'. See catia_list_relations.")
        relations.Remove(name)
        part.Update()
        return f"Relation '{name}' deleted."

    def _create_design_table(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        params = part.Parameters
        path = args["file_path"]
        if not os.path.exists(path):
            raise RuntimeError(
                f"No sheet at '{path}'. The path is read on the CATIA machine, not "
                "where this server runs, and it must be absolute."
            )
        names = list(args["parameters"])
        missing = [n for n in names if self._find_parameter(params, n) is None]
        if missing:
            hint = ""
            for name in missing:
                near = self._near_names(params, name)
                if near:
                    hint += (f" '{name}' may be {', '.join(repr(n) for n in near)};")
            raise RuntimeError(
                f"Unknown parameter(s): {', '.join(missing)}. Create them first "
                "with catia_create_parameter." + (f" Near matches:{hint}" if hint else "")
            )
        table = part.Relations.CreateDesignTable(
            args["name"], args.get("comment") or "",
            bool(args.get("copy_mode", True)), path
        )
        for n in names:
            table.AddAssociation(params.Item(n), n)
        part.Update()
        rows = "?"
        try:
            rows = table.ConfigurationsNb
        except Exception:  # noqa: BLE001
            pass
        return (
            f"Design table '{table.Name}' created from '{path}' with "
            f"{len(names)} association(s): {', '.join(names)}. "
            f"Rows: {rows}. " + _TABLE_UNIT_NOTE +
            " Switch to a row with catia_apply_design_table."
        )

    def _apply_design_table(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        name = args["name"]
        try:
            table = part.Relations.Item(name)
        except Exception as error:  # noqa: BLE001
            raise RuntimeError(
                f"No relation named '{name}'. See catia_list_relations; only a "
                "design table can be applied."
            ) from error
        try:
            total = int(table.ConfigurationsNb)
        except Exception as error:  # noqa: BLE001
            raise RuntimeError(
                f"'{name}' does not report a configuration count, so it is probably "
                f"not a design table ({str(error).splitlines()[0][:90]})."
            ) from error
        wanted = int(args["configuration"])
        if wanted < 1 or wanted > total:
            raise RuntimeError(
                f"Configuration {wanted} is out of range: '{name}' has {total} "
                "row(s), numbered from 1."
            )
        table.Configuration = wanted
        try:
            part.Update()
        except Exception as error:  # noqa: BLE001
            raise RuntimeError(
                f"Configuration {wanted} was set but the part update failed "
                f"({str(error).splitlines()[0][:110]}). The part may be in an "
                "intermediate state - check it in CATIA."
            ) from error
        return (
            f"Design table '{name}' applied configuration {wanted} of {total}. "
            "Every associated parameter now holds that row's value; read them with "
            "catia_get_parameters. A length cell without a unit is read in metres, "
            "so a value 1000x too large means the sheet needed '30mm'."
        )
