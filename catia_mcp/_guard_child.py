"""Child process for :mod:`catia_mcp.guard` - one COM call, then exit.

Started as ``python -m catia_mcp._guard_child <args.json> <result.json>``.

Rules for this file:

* **Attach, never launch.** ``GetActiveObject`` only. A ``Dispatch`` here would
  start a second CATIA when none is running, which is worse than the dialog it is
  supposed to protect against.
* **Do not touch the parent's streams.** stdout/stderr go nowhere useful; the
  answer is written to the result file so a partially written stdout can never be
  mistaken for a result.
* **Do not save, close or activate anything.** The only state this process may
  change is what the operation itself asks for.
"""

from __future__ import annotations

import json
import os
import sys
import traceback

PROGID = "CATIA.Application"


def _attach():
    import pythoncom  # noqa: PLC0415 - only this process needs it
    import win32com.client  # noqa: PLC0415

    pythoncom.CoInitialize()
    return win32com.client.GetActiveObject(PROGID)


def _op_ping(app, payload):
    names = [app.Documents.Item(i).Name for i in range(1, app.Documents.Count + 1)]
    try:
        active = app.ActiveDocument.Name
    except Exception:  # noqa: BLE001 - nothing active
        active = ""
    return True, "OK", "", {"documents": names, "active": active}


def _op_add_components(app, payload):
    """The call that can put up a file dialog - the reason this process exists."""
    path = os.path.normpath(str(payload["path"]))
    wanted_active = payload.get("active_document")
    try:
        doc = app.ActiveDocument
    except Exception:  # noqa: BLE001
        return False, "NO_ACTIVE_DOCUMENT", "CATIA has no active document", {}
    if wanted_active and str(doc.Name) != str(wanted_active):
        return (False, "ACTIVE_DOCUMENT_CHANGED",
                f"the active document is '{doc.Name}', not '{wanted_active}'; the "
                "insertion would land in the wrong assembly", {})
    try:
        product = doc.Product
    except Exception:  # noqa: BLE001
        return (False, "NOT_AN_ASSEMBLY",
                f"'{doc.Name}' is not a Product document; activate the assembly "
                "first", {"document": str(doc.Name)})

    products = product.Products
    before = products.Count
    alerts = None
    try:
        try:
            alerts = app.DisplayFileAlerts
            app.DisplayFileAlerts = False
        except Exception:  # noqa: BLE001 - not every build exposes it
            alerts = None
        products.AddComponentsFromFiles([path], "All")
    finally:
        if alerts is not None:
            try:
                app.DisplayFileAlerts = alerts
            except Exception:  # noqa: BLE001
                pass
    after = products.Count
    names = []
    for index in range(before + 1, after + 1):
        try:
            names.append(str(products.Item(index).Name))
        except Exception:  # noqa: BLE001
            pass
    data = {"path": path, "before": before, "after": after, "added": names}
    if after <= before:
        return (False, "NO_COMPONENT_ADDED",
                f"CATIA accepted the call but the component count stayed at {before}",
                data)
    return True, "OK", "", data


OPS = {"ping": _op_ping, "add_components": _op_add_components}


def main(argv: list[str]) -> int:
    args_path, result_path = argv[1], argv[2]
    with open(args_path, encoding="utf-8") as handle:
        request = json.load(handle)
    op = request["op"]
    payload = request.get("payload") or {}
    result: dict[str, object] = {"ok": False, "code": "GUARD_ERROR", "message": ""}
    try:
        app = _attach()
    except Exception as exc:  # noqa: BLE001 - the parent falls back in-process
        result = {"ok": False, "code": "GUARD_NO_CATIA",
                  "message": f"could not attach to the running CATIA: {exc}"}
    else:
        handler = OPS.get(op)
        if handler is None:
            result = {"ok": False, "code": "GUARD_UNKNOWN_OP",
                      "message": f"unknown operation '{op}'"}
        else:
            try:
                ok, code, message, data = handler(app, payload)
                result = {"ok": ok, "code": code, "message": message, "data": data}
            except Exception as exc:  # noqa: BLE001 - report, never hang
                result = {"ok": False, "code": "COM_ERROR",
                          "message": f"{type(exc).__name__}: {exc}".splitlines()[0][:300],
                          "data": {"traceback": traceback.format_exc()[-800:]}}
    tmp = result_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False)
    os.replace(tmp, result_path)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
