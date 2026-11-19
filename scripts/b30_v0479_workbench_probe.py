"""Is the assembly workbench actually active?  (v0479, second attempt)

v0478 showed StartCommand('Fix') answering "未知命令: Fix" in a message box and
every AddMonoEltCst form answering E_FAIL.  Both are consistent with one
condition no earlier round verified: the active workbench is not Assembly
Design, so neither the command nor the constraint engine is in scope - and
StartWorkbench's return value (always None, even for a name that does not
exist) cannot tell us whether it switched.

Measured by consequence, with a control in the same session:

  A  control: Fix on a SKETCH (catCstTypeReference = 0, a Reference, this same
     CATIA).  If that works, the method and the marshalling are fine and the
     assembly collection is what refuses.
  B  StartCommand('Fix') with a component selected, before and after
     StartWorkbench('Assembly Design' / '装配设计') - "未知命令" is a workbench
     verdict, and it is read off a message box, not off a return value.
  C  all constraint type codes 0..13 against the product reference.
  D  the same against a child product's own collection.

COM is apartment-threaded: every StartCommand runs on THIS thread (which called
CoInitialize); only the message-box poller runs on a helper thread.
"""
from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "catia_probe_v0479")
os.makedirs(ROOT, exist_ok=True)
USER32 = ctypes.windll.user32
DIALOG_TITLE = "超级输入消息"


def say(text: str) -> None:
    print(text, flush=True)


def windows(title: str) -> list[int]:
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _lp):
        n = USER32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            USER32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value.strip() == title:
                found.append(hwnd)
        return True

    USER32.EnumWindows(cb, None)
    return found


def dismiss(title: str = DIALOG_TITLE) -> bool:
    """Click the OK button of CATIA's message box, if it is up."""
    hit = False
    for hwnd in windows(title):
        buttons: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def cb(h, _lp):
            cls = ctypes.create_unicode_buffer(64)
            USER32.GetClassNameW(h, cls, 64)
            if cls.value == "Button":
                buttons.append(h)
            return True

        USER32.EnumChildWindows(hwnd, cb, None)
        for button in buttons:
            USER32.SendMessageW(button, 0x00F5, 0, 0)  # BM_CLICK
        hit = True
    if hit:
        time.sleep(0.4)
    return hit


def command(app, name: str, timeout: float = 8.0) -> str:
    """Run a UI command on this thread; a message box means 'unknown command'."""
    seen = threading.Event()
    stop = threading.Event()

    def poller():
        while not stop.is_set():
            if dismiss():
                seen.set()
                return
            time.sleep(0.2)

    watcher = threading.Thread(target=poller, daemon=True)
    watcher.start()
    verdict = ""
    try:
        app.StartCommand(name)
        verdict = "returned (command known)"
    except Exception as exc:  # noqa: BLE001
        verdict = f"raised {type(exc).__name__}: {exc}"
    deadline = time.time() + timeout
    while time.time() < deadline and not seen.is_set() and watcher.is_alive():
        time.sleep(0.2)
    stop.set()
    watcher.join(timeout=1.5)
    if seen.is_set():
        return "UNKNOWN COMMAND (message box)"
    if watcher.is_alive():
        return f"BLOCKED on a message box this probe does not dismiss ({verdict})"
    return verdict


def probe_attr(obj, name: str) -> str:
    try:
        value = getattr(obj, name)
        return f"{name} -> {value!r}" if not callable(value) else f"{name} -> <callable>"
    except Exception as exc:  # noqa: BLE001
        return f"{name} -> AttributeError"


def short(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text[:110]


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    app = conn.app
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    asm = AssemblyTools(conn)

    say(f"probe v0479 on {app.Name}")
    dismiss()

    try:
        while True:
            docs.execute("catia_close_document", {"save": False})
    except Exception:  # noqa: BLE001
        pass

    say("\napp members that could name a workbench:")
    for name in ("ActiveWorkbench", "ActiveWorkbenchName", "GetWorkbenchId",
                 "StartCommand", "StartWorkbench", "ActiveWindow", "ActiveEditor"):
        say("  " + probe_attr(app, name))

    # ══ A: control - Fix on a SKETCH, same code 0, same method =============
    say("\nA: control - Fix (code 0) on a sketch in this same CATIA")
    part_path = os.path.join(ROOT, "CstControl.CATPart")
    if os.path.exists(part_path):
        os.remove(part_path)
    docs.execute("catia_new_part", {"name": "CstControl", "file_path": part_path})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "S1"})
    sketcher.execute("catia_sketch_line", {"x1": 0, "y1": 0, "x2": 20, "y2": 0})
    try:
        reply = sketcher.execute("catia_sketch_constraint",
                                 {"type": "fix", "geometry_index_1": 2})
        say("  sketch fix constraint: " + " ".join(str(reply).split())[:160])
    except Exception as exc:  # noqa: BLE001
        say(f"  sketch fix FAILED: {type(exc).__name__}: {short(exc)}")

    # ══ B: the assembly, and what the workbench says ========================
    say("\nB: product + Part.1, then StartCommand('Fix') before/after switching")
    asm_path = os.path.join(ROOT, "CstAsm.CATProduct")
    if os.path.exists(asm_path):
        os.remove(asm_path)
    docs.execute("catia_new_product", {"name": "CstAsm", "file_path": asm_path})
    product = conn.get_active_product()
    asm.execute("catia_add_new_part", {})
    comp = product.Products.Item(1)
    sel = conn.hso

    def fix_now(label: str) -> None:
        sel.Clear()
        sel.Add(comp)
        before = product.Connections("CATIAConstraints").Count
        verdict = command(app, "Fix")
        after = product.Connections("CATIAConstraints").Count
        say(f"  {label}: StartCommand('Fix') -> {verdict}; "
            f"constraints {before} -> {after}")

    fix_now("as placed")
    for name in ("Assembly Design", "装配设计", "AssemblyDesign"):
        try:
            switched = app.StartWorkbench(name)
        except Exception as exc:  # noqa: BLE001
            switched = f"raised {short(exc)}"
        say(f"  StartWorkbench({name!r}) -> {switched!r}")
        time.sleep(1.5)
        fix_now(f"after {name!r}")

    # ══ C: every constraint type code, against the product reference =======
    say("\nC: AddMonoEltCst type codes 0..13 on the product selection reference")
    sel.Clear()
    sel.Add(comp)
    reference = sel.Item(1).Reference
    constraints = product.Connections("CATIAConstraints")
    for code in range(14):
        try:
            cst = constraints.AddMonoEltCst(code, reference)
            say(f"  code {code:2d}: CREATED {cst.Name!r}, count={constraints.Count}")
        except Exception as exc:  # noqa: BLE001
            say(f"  code {code:2d}: {short(exc)}")

    # ══ D: a child product's own collection ===============================
    say("\nD: the child product's own Connections('CATIAConstraints')")
    try:
        child_constraints = comp.Connections("CATIAConstraints")
        say(f"  child collection Count: {child_constraints.Count}")
        for code in (0, 1, 2, 3):
            try:
                cst = child_constraints.AddMonoEltCst(code, reference)
                say(f"  code {code}: CREATED {cst.Name!r}")
            except Exception as exc:  # noqa: BLE001
                say(f"  code {code}: {short(exc)}")
    except Exception as exc:  # noqa: BLE001
        say(f"  child Connections failed: {short(exc)}")

    dismiss()
    say("\ndone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
