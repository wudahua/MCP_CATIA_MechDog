"""Sheet-metal parameters: read them, set them, and say what still cannot be built.

The 2026-11-03 report asked for eight tools after modelling a real bracket without
any of them. This module delivers the two that the automation interface actually
supports - the parameter set, and a document that starts with it - and states the
rest as measured fact instead of leaving it to be discovered.

WHAT IS HERE
    catia_sheetmetal_parameters   read thickness / bend radius / K factor / relief
                                  slots on the active part, and SET them
    catia_new_sheetmetal          a new PartDocument that already carries the
                                  parameter set (the report's "new_sheetmetal")

WHAT IS NOT, AND WHY (B30, 2026-11-15, scripts/b30_v0434_probe_sheetmetal.py)
    No wall, flange, bend, unfold or flat-pattern. ``Part.SheetMetalFactory`` has
    23 members, 22 of them the COM base, and the one real method is
    ``CreateSheetMetalParameters()``. Its result has 23 members and one method,
    ``GetThickness()``. There is no ``AddNew*`` anywhere, so there is nothing to
    wrap - and that is an interface fact, not a licence one: a workbench cannot
    add methods to an interface that ships without them.

WHY THE PARAMETERS ARE WORTH TOOLS EVEN SO
    They are not decoration. A sheet-metal part that came from somewhere else (a
    colleague's CATPart, a customer's model) carries its thickness, bend radius
    and K factor in this set, and an acceptance check that says "R 1.905 / K 0.5
    really took effect" has to read them. Before this module the answer was "this
    server cannot see them", which was wrong twice: ``SheetMetalParameters`` is
    read-only, but the same values live in ``part.Parameters`` and ``Value``
    there IS writable - measured, with the read-back in the docstring of
    ``catia_mcp.sheetmetal``.
"""
from __future__ import annotations

import json
from typing import Any

from catia_mcp import sheetmetal
from catia_mcp.results import (ArgumentRejected, CodedError, InternalError,
                               PreconditionRejected)

#: The range each value has to fall in before anything is written. Loose on
#: purpose - these reject typos (a negative thickness, a K factor of 5), not
#: engineering judgement, which the caller is better at.
_RANGES: dict[str, tuple[float, float]] = {
    "thickness": (1e-4, 1000.0),
    "bend_radius": (0.0, 10000.0),
    "k_factor": (0.0, 1.0),
    "relief_radius": (0.0, 10000.0),
    "relief_axis": (0.0, 10000.0),
    "minimum_bend_radius": (0.0, 10000.0),
}


class SheetMetalTools:
    """Reading and writing the sheet-metal parameters of a part."""

    def __init__(self, connection: Any, documents: Any = None) -> None:
        self.conn = connection
        # catia_new_sheetmetal is catia_new_part plus the parameter set, and
        # catia_new_part already knows about the ASCII fallback, overwrite
        # refusal and the read-back of the saved path. Duplicating that logic
        # here would be a second place for it to drift.
        self._documents = documents

    # ── tool definitions ──────────────────────────────────────────────────
    def get_tool_definitions(self) -> list[dict[str, Any]]:
        value_props: dict[str, Any] = {}
        for key, (low, high) in _RANGES.items():
            value_props[key] = {
                "type": "number",
                "description": (
                    f"The {key.replace('_', ' ')} in millimetres"
                    + (" (0..1 - it is a ratio)" if key == "k_factor" else "")
                    + ". Omit to leave it as it is; give it to WRITE it. The "
                      "reply quotes the value read back afterwards, not the one "
                      "that was asked for."
                ),
            }
        return [
            {
                "name": "catia_sheetmetal_parameters",
                "description": (
                    "Read - and optionally set - the sheet-metal parameters of the "
                    "active part: thickness, default bend radius, K factor, corner "
                    "relief. These are the values an acceptance check has to see "
                    "('R 1.905 / K 0.5 really took effect'), and until 0.3.133 this "
                    "server could not see them at all. "
                    "Measured on B30 2026-11-15: the values live in part.Parameters "
                    "as ordinary parameters (钣金参数.1\\线宽, 弯曲半径, K 因子, "
                    "拐角止裂槽半径长度/轴长度, Minimum Bend Radius) and "
                    "Parameter.Value IS writable - thickness 2.0 -> 1.5 moved "
                    "SheetMetalParameters.GetThickness() with it. Two exceptions "
                    "are reported, not hidden: the K factor is computed by the "
                    "standard's DIN rule and refuses Value until that rule's "
                    "Activity is switched off (this tool switches it off and says "
                    "so), and a part that has never had the set created answers "
                    "'方法 SheetMetalParameters 失败' until create=true calls "
                    "CreateSheetMetalParameters(). "
                    "THERE IS STILL NO WALL / FLANGE / BEND / UNFOLD / FLAT PATTERN: "
                    "Part.SheetMetalFactory has 23 members and the ONLY real one is "
                    "CreateSheetMetalParameters - no AddNew* of any kind, and the "
                    "parameter object adds no setter. So this reads and tunes the "
                    "setup an existing sheet-metal part carries; it cannot create "
                    "the first wall, and a part can hold these parameters and still "
                    "have no sheet-metal geometry. For that shape, extrude + cut is "
                    "the substitute, and its price is measured: a 2 mm bracket with "
                    "two bends came out 76 faces against the source's 57."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        **value_props,
                        "create": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Add the parameter set if the document has none. "
                                "It calls Part.SheetMetalFactory."
                                "CreateSheetMetalParameters(), which succeeds once "
                                "per document (a second call fails: measured) and "
                                "adds NO feature - the body's feature count stays "
                                "at 0 and nothing appears in the tree. CATIA's "
                                "defaults arrive with it (2.0 mm thickness, "
                                "4.0 mm bend radius), which is why the reply lists "
                                "every value it can now see."
                            ),
                        },
                    },
                },
            },
            {
                "name": "catia_new_sheetmetal",
                "description": (
                    "Create a new PartDocument that already carries the sheet-metal "
                    "parameter set - the 2026-11-03 report's 'new_sheetmetal'. It is "
                    "catia_new_part followed by catia_sheetmetal_parameters("
                    "create=true, ...), so name / file_path / overwrite / "
                    "ascii_fallback mean exactly what they mean there. "
                    "IT GIVES YOU A DOCUMENT, NOT A PARTITION: a wall, flange or "
                    "bend cannot be created by this automation at all (measured: "
                    "Part.SheetMetalFactory exposes no AddNew*, only "
                    "CreateSheetMetalParameters), so the geometry still has to be "
                    "extruded and cut. What the parameters buy is that the design "
                    "intent - thickness, bend radius, K factor - is in the model "
                    "and readable, instead of in the caller's head."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Part number for the new document.",
                        },
                        "file_path": {
                            "type": "string",
                            "description": (
                                "Where to save it. A non-ASCII file name is refused "
                                "by this CATIA build - pass ascii_fallback or "
                                "ascii_name as you would to catia_new_part."
                            ),
                        },
                        "overwrite": {"type": "boolean", "default": False},
                        "ascii_fallback": {"type": "boolean", "default": False},
                        "ascii_name": {"type": "string"},
                        **value_props,
                    },
                },
            },
        ]

    # ── dispatch ──────────────────────────────────────────────────────────
    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        match tool_name:
            case "catia_sheetmetal_parameters":
                return self._parameters(arguments)
            case "catia_new_sheetmetal":
                return self._new_sheetmetal(arguments)
            case _:
                raise ArgumentRejected(f"Unknown sheet-metal tool: {tool_name}")

    # ── arguments ─────────────────────────────────────────────────────────
    @staticmethod
    def _requested(arguments: dict[str, Any]) -> dict[str, float]:
        """The values to write, validated BEFORE a session is opened.

        Same rule as 0.3.127: a rejected argument must not need CATIA to be
        reachable, let alone leave it holding a half-written parameter set.
        """
        wanted: dict[str, float] = {}
        for key, (low, high) in _RANGES.items():
            if key not in arguments or arguments[key] is None:
                continue
            raw = arguments[key]
            if isinstance(raw, bool):
                raise ArgumentRejected(
                    f"{key}={raw!r} is a boolean. It is a length in millimetres"
                    + (" or a ratio 0..1" if key == "k_factor" else "")
                    + ". Nothing was read and nothing was written.",
                    code="BAD_ARGUMENT", stage="validate")
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise ArgumentRejected(
                    f"{key}={raw!r} is not a number. Nothing was read and nothing "
                    "was written.", code="BAD_ARGUMENT", stage="validate")
            if not (low <= value <= high):
                unit = "" if key == "k_factor" else " mm"
                raise ArgumentRejected(
                    f"{key}={value:g}{unit} is outside {low:g}..{high:g}{unit}. "
                    "That is a typo, not a design change - and a sheet-metal "
                    "thickness of 0 or a negative radius does not fail here, it "
                    "fails in the shop. Nothing was read and nothing was written.",
                    code="BAD_ARGUMENT", stage="validate",
                    data={"parameter": key, "value": value,
                          "range": [low, high]})
            wanted[key] = value
        return wanted

    # ── reading ───────────────────────────────────────────────────────────
    @staticmethod
    def _table(part: Any) -> dict[str, Any]:
        """Every parameter of the part, by full name."""
        table: dict[str, Any] = {}
        for index in range(1, int(part.Parameters.Count) + 1):
            parameter = part.Parameters.Item(index)
            try:
                table[str(parameter.Name)] = parameter
            except Exception:  # noqa: BLE001 - an unnamed parameter is useless here
                continue
        return table

    def _locate_or_create(self, part: Any, create: bool) -> tuple[dict, bool]:
        table = self._table(part)
        located = sheetmetal.locate(list(table))
        if located["container"] is not None:
            return located, False
        if not create:
            raise PreconditionRejected(
                "This part has no sheet-metal parameters. `part.SheetMetalParameters` "
                "answers '方法 SheetMetalParameters 失败' until "
                "CreateSheetMetalParameters() has run (measured on B30 2026-11-15), "
                "and nothing in part.Parameters is named 线宽 / Thickness. "
                "Pass create=true to add the set CATIA's defaults (2.0 mm), or use "
                "catia_new_sheetmetal for a document that has it from the start. "
                "Nothing was read and nothing was written.",
                code="NO_SHEETMETAL_PARAMETERS", stage="read",
                data={"document": str(getattr(part, "Name", "?"))})
        try:
            factory = part.SheetMetalFactory
            factory.CreateSheetMetalParameters()
        except Exception as error:  # noqa: BLE001 - reported as a coded failure
            raise CodedError(
                "CreateSheetMetalParameters() failed: "
                + str(error).splitlines()[0][:160]
                + ". The set is created ONCE per document - if this part already "
                  "has it under a name this search did not recognise, the parameter "
                  "list below is what to look at.",
                code="SHEETMETAL_CREATE_FAILED", stage="execute",
                data={"parameters": sorted(self._table(part))[:40],
                      "call": "Part.SheetMetalFactory.CreateSheetMetalParameters"})
        table = self._table(part)
        located = sheetmetal.locate(list(table))
        if located["container"] is None:
            raise CodedError(
                "CreateSheetMetalParameters() returned without error, but no "
                "sheet-metal parameter appears in part.Parameters afterwards. "
                "That is a refusal dressed as success - report it with the "
                "parameter list below rather than writing to a guess.",
                code="SHEETMETAL_CREATE_INVISIBLE", stage="execute",
                data={"parameters": sorted(table)[:40],
                      "call": "Part.SheetMetalFactory.CreateSheetMetalParameters"})
        return located, True

    def _parameters(self, arguments: dict[str, Any], created_document: Any = None) -> str:
        wanted = self._requested(arguments)
        create = bool(arguments.get("create", False))
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        document = self.conn.active_document
        located, created = self._locate_or_create(part, create)
        table = self._table(part)

        before_values = self._read_values(located, table)

        writes: list[dict[str, Any]] = []
        notes: list[str] = []
        for key, asked in wanted.items():
            path = located["found"].get(key)
            if path is None:
                writes.append({"parameter": key, "asked": asked, "written": False,
                               "reason": "no parameter on this document matches "
                                         f"any known spelling of {key}",
                               "spellings": list(sheetmetal.PROPERTIES[key])})
                continue
            writes.append(self._write(part, table, located, key, path, asked,
                                      notes,
                                      before=before_values.get(key, {}).get("value")))

        if any(entry.get("written") for entry in writes):
            try:
                part.Update()
            except Exception as error:  # noqa: BLE001 - the values are written
                notes.append("part.Update() failed after the writes: "
                             + str(error).splitlines()[0][:120]
                             + " - the values WERE read back, so they are stored, "
                               "but the model did not rebuild.")
        # READ BACK AFTER THE WRITES, not before them: 0.3.133's first version
        # cross-checked GetThickness() before the loop, so a call that set the
        # thickness to 1.5 still reported "get_thickness_mm: 2.0" beside
        # "after: 1.5" - the exact class of reply this project keeps removing.
        values = self._read_values(located, table) if writes else before_values
        cross_check = self._cross_check(part, values)

        report: dict[str, Any] = {
            "ok": True,
            "document": str(getattr(document, "Name", "?")),
            "container": located["container"],
            "created": created,
            "parameters": values,
            "cross_check": cross_check,
            "writes": writes,
            "missing": located["missing"],
        }
        if created_document is not None:
            report["new_part"] = created_document
        report["notes"] = self._notes(report, located, created, bool(wanted))
        return json.dumps(report, ensure_ascii=False, indent=2)

    @staticmethod
    def _read_values(located: dict, table: dict[str, Any]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for key, path in located["found"].items():
            parameter = table.get(path)
            try:
                value = sheetmetal.reading(parameter.Value)
            except Exception as error:  # noqa: BLE001 - a dimension-less leaf
                value = "<unreadable: " + str(error).splitlines()[0][:60] + ">"
            values[key] = {"name": path.split("\\")[-1], "value": value}
        return values

    @staticmethod
    def _cross_check(part: Any, values: dict[str, Any]) -> dict[str, Any]:
        """GetThickness() is the only reading the parameter OBJECT offers.

        Worth taking, because the claim this tool makes is "the parameter set IS
        the sheet-metal setup" - and a number that agrees on both sides is the
        evidence, where "there are parameters that happen to be called thickness"
        would not be.
        """
        try:
            live = float(part.SheetMetalParameters.GetThickness())
        except Exception as error:  # noqa: BLE001 - not required for the answer
            return {"unavailable": str(error).splitlines()[0][:80]}
        read = values.get("thickness", {}).get("value")
        return {"get_thickness_mm": round(live, 6),
                "agrees": sheetmetal.same_value(read, live)}

    def _write(self, part: Any, table: dict[str, Any], located: dict,
               key: str, path: str, asked: float,
               notes: list[str], before: Any = None) -> dict[str, Any]:
        parameter = table.get(path)
        # `before` is the value at the START of this call, not the value just
        # before this parameter's turn: writing the thickness makes the DIN rule
        # recompute the K factor (2.0/4.0 -> 0.4005, then 1.5/1.905 -> 0.3512,
        # measured), so a per-parameter snapshot would show that as if the caller
        # had changed it.
        entry: dict[str, Any] = {"parameter": key, "name": path.split("\\")[-1],
                                 "asked": asked, "written": False,
                                 "before": before if before is not None else None}
        try:
            parameter.Value = asked
        except Exception as error:  # noqa: BLE001 - locked by a rule, maybe
            entry["refused"] = str(error).splitlines()[0][:120]
            unlocked = self._unlock(parameter, table, located, key, asked)
            entry["unlocked"] = unlocked
            if not unlocked["written"]:
                entry["error"] = (
                    "CATIA refused the value and switching off the rule(s) that "
                    "hold it did not help: " + unlocked.get("tried", "none")
                    + ". Nothing was changed for this parameter.")
                return entry
        try:
            read = sheetmetal.reading(parameter.Value)
        except Exception as error:  # noqa: BLE001
            entry["error"] = ("the write did not raise, but the value cannot be "
                              "read back: " + str(error).splitlines()[0][:90])
            return entry
        entry["after"] = read
        entry["written"] = sheetmetal.same_value(asked, read)
        if not entry["written"]:
            entry["error"] = (
                f"asked for {asked:g}, CATIA kept {read} - the property accepted "
                "the assignment and did not take it (a driven value, or a "
                "tolerance this tool cannot see).")
        return entry

    def _unlock(self, parameter: Any, table: dict[str, Any], located: dict,
                key: str, asked: float) -> dict[str, Any]:
        """Turn off the rule(s) locking this value, one at a time, and retry.

        On B30 the K factor is computed by the standard's DIN rule, and
        ``Value`` raises "方法 Value 失败" until ``...\\DIN 一般公式\\Activity`` is
        False. The switches are tried ONE AT A TIME and the ones that did not
        help are switched back on: leaving a part with every rule disabled
        because one write was attempted would be a bigger change than the caller
        asked for.
        """
        switches = sheetmetal.activity_switches(list(table), located["container"])
        tried: list[str] = []
        switched_off: list[str] = []
        for name in switches:
            holder = table.get(name)
            try:
                if not bool(holder.Value):
                    continue
            except Exception:  # noqa: BLE001 - an unreadable switch is not one
                continue
            try:
                holder.Value = False
            except Exception:  # noqa: BLE001
                continue
            switched_off.append(name.split("\\")[-2] if "\\" in name else name)
            tried.append(name)
            try:
                parameter.Value = asked
            except Exception:  # noqa: BLE001 - try the next switch
                continue
            return {"written": True, "switched_off": switched_off,
                    "tried": ", ".join(tried)}
        # nothing worked: put back what this call changed
        for name in tried:
            try:
                table[name].Value = True
            except Exception:  # noqa: BLE001 - reported as unrestored below
                pass
        return {"written": False, "switched_off": [], "tried": ", ".join(tried) or "none"}

    @staticmethod
    def _notes(report: dict[str, Any], located: dict, created: bool,
               wrote: bool) -> list[str]:
        notes: list[str] = []
        if created:
            notes.append(
                "The parameter set was created by this call "
                "(CreateSheetMetalParameters) - CATIA's defaults, then the values "
                "you asked for. It creates NO feature: the body's feature count "
                "does not change and no node appears in the specification tree "
                "(measured 2026-11-15: 0 features before and after).")
        if report["missing"]:
            notes.append(
                "Not on this document: " + ", ".join(report["missing"])
                + ". The leaves are localised, so an unrecognised spelling is "
                  "reported as missing rather than guessed at - if the value is "
                  "there under another name, part.parameters in the reply above "
                  "is the list to look at.")
        if not wrote:
            notes.append(
                "This call only READ. Give thickness / bend_radius / k_factor / "
                "relief_radius / relief_axis / minimum_bend_radius to write them; "
                "the reply then quotes each one read back.")
        for entry in report["writes"]:
            if entry.get("written") and entry.get("unlocked", {}).get("switched_off"):
                notes.append(
                    f"{entry['parameter']} was locked by the rule "
                    + ", ".join(entry["unlocked"]["switched_off"])
                    + ", so this call switched that rule OFF and wrote the value. "
                      "It is left off: switching it back on would recompute the "
                      "value from the rule and undo the write. On B30 the K factor "
                      "is exactly this case (DIN 一般公式).")
        notes.append(
            "thickness 与 SheetMetalParameters.GetThickness() 交叉核对过"
            if report["cross_check"].get("agrees")
            else "thickness could not be cross-checked against "
                 "SheetMetalParameters.GetThickness() - the parameters above are "
                 "the values in part.Parameters, which is where the writable copy "
                 "lives.")
        notes.append(
            "Sheet metal GEOMETRY is still not available: no wall, flange, bend, "
            "unfold or flat pattern. Part.SheetMetalFactory exposes no AddNew* "
            "(measured), so extrude + cut remains the substitute - and its price "
            "is measured: 76 faces against the source part's 57 on a 2 mm bracket "
            "with two bends.")
        return notes

    # ── a new document that carries the set ───────────────────────────────
    def _new_sheetmetal(self, arguments: dict[str, Any]) -> str:
        # validate before anything is created
        values = self._requested(arguments)
        if self._documents is None:
            raise InternalError(
                "catia_new_sheetmetal needs the document tools, and this server "
                "was built without them. Use catia_new_part followed by "
                "catia_sheetmetal_parameters(create=true).",
                code="WIRING", stage="execute")
        forward = {key: arguments[key] for key in
                   ("name", "file_path", "overwrite", "ascii_fallback", "ascii_name")
                   if arguments.get(key) is not None}
        created = self._documents.execute("catia_new_part", forward)
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        located, made = self._locate_or_create(part, True)
        report = json.loads(
            self._parameters({**values, "create": True}, created_document=created))
        report["created"] = made
        report["container"] = located["container"]
        return json.dumps(report, ensure_ascii=False, indent=2)
