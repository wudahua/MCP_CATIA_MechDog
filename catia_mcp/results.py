"""Structured failures without changing server-wide session availability."""

from __future__ import annotations

from typing import Any
from uuid import uuid4


class FeatureRejected(RuntimeError):
    """CATIA refused a feature, and the tool has already dealt with the corpse.

    ``rolled_back`` is the fact the caller needs and cannot get from the text: a
    rejected feature that could not be removed poisons the whole part (every
    later Update fails), while one that was removed leaves the model exactly as
    it was. Carrying it as an attribute lets the error envelope agree with the
    message instead of contradicting it - reported twice on 2026-10-25, because
    ``next_actions`` said "recovery is manual" even when the tool had just undone
    the change.

    ``next_actions`` is what the caller should do NEXT, and only the raise site
    knows it: "fix the argument and retry" is wrong for a pad that built no
    material in either direction, because the retry is the call that just
    failed. Reported 2026-10-28: the message said "rolled back, volume stayed
    96 750.0, both directions were measured" while the envelope said
    ``rolled_back: null`` and "model changes were not verified".
    """

    def __init__(self, message: str, rolled_back: bool,
                 next_actions: list[str] | None = None) -> None:
        super().__init__(message)
        self.rolled_back = bool(rolled_back)
        if next_actions:
            self.next_actions = list(next_actions)


class CodedError(RuntimeError):
    """A deliberate refusal that carries the code a caller should branch on.

    ``catia_run_plan`` used to classify a failed step by reading the message
    ("if 'no component matches' in text"), so a wording change silently
    downgraded the code to ``STEP_EXCEPTION`` - reported 2026-10-27, where
    ``catia_get_bounding_box`` answered ``COMPONENT_NOT_FOUND`` for a name while
    ``catia_get_component_transform`` answered ``STEP_EXCEPTION`` for the same
    name, because only the first phrase was in the table. The raise site knows
    what it is refusing; the code travels with the exception from now on, and the
    phrase table stays only for failures CATIA itself produces.

    ``modified`` and ``next_actions`` travel the same way. A refusal that happens
    before anything is written knows it wrote nothing - "No sketch named 'X'.
    Available: ..." is a lookup in a list - and leaving that as the envelope's
    "unknown" put "model changes were not verified, inspect the CATIA model" on
    a call that never touched the model (reported 2026-10-28, §5.2).

    ``data`` carries the structured detail a message cannot: an ambiguous
    document name publishes the candidates themselves, so the caller can pick one
    instead of parsing prose out of an error string (reported 2026-10-28, P1).
    """

    code = "STEP_EXCEPTION"
    modified: bool | None = None

    def __init__(self, message: str, code: str | None = None,
                 modified: bool | None = None,
                 next_actions: list[str] | None = None,
                 data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = str(code)
        if modified is not None:
            self.modified = bool(modified)
        if next_actions:
            self.next_actions = list(next_actions)
        if data:
            self.data = dict(data)


class ComponentNotFound(CodedError, ValueError):
    """No component with that name in the active assembly.

    A ``ValueError`` as well, so callers that already catch that keep working.
    """

    code = "COMPONENT_NOT_FOUND"


def failure_code(message: str, error: BaseException | None = None,
                default: str = "STEP_EXCEPTION") -> str:
    """A code a script can branch on: the exception's own, else read the message.

    ONE table, used by both paths. Before 0.3.34 ``catia_run_plan`` classified a
    failed step by reading the message while a bare call classified it by the
    exception TYPE, so the same failed pad answered ``NO_MATERIAL`` inside a plan
    and ``COM_ERROR`` on its own - two semantics for one failure, in one server
    (reported 2026-10-28, as a P0 regression against 0.3.30).

    The order is deliberate: what the raise site declares wins, because a phrase
    table is only as good as the last person to edit the wording
    (``ComponentNotFound`` was added for exactly that reason on 2026-10-27).
    ``AttributeError`` means the automation API did not resolve, which no table
    entry can know better than the exception type.
    """
    carried = getattr(error, "code", None)
    if isinstance(carried, str) and carried and carried != "STEP_EXCEPTION":
        return carried
    if isinstance(error, AttributeError):
        return "UNSUPPORTED_CAPABILITY"
    text = (message or "").casefold()
    if "no sketch named" in text or "no sketch" in text and "available" in text:
        return "SKETCH_NOT_FOUND"
    if "built no material" in text or "removed no material" in text:
        return "NO_MATERIAL"
    if (message or "").startswith("UNSUPPORTED_CAPABILITY"):
        return "UNSUPPORTED_CAPABILITY"
    if "no component matches" in text or "no component '" in text:
        return "COMPONENT_NOT_FOUND"
    if ("no open document matches" in text or "no open document named" in text
            or "is not open" in text and "open:" in text):
        # "I typed the name wrong / it is not open" and "CATIA is broken" need
        # different next steps, and COM_ERROR said neither (reported 2026-10-28,
        # §5.3).
        return "DOCUMENT_NOT_FOUND"
    if "part not loaded" in text:
        return "PART_NOT_LOADED"
    return default


# A failure of a tool that cannot change the document must not carry recovery
# advice about repairing one: reported 2026-10-28, where
# catia_get_bounding_box{document: '<a document that is not open>'} answered "no
# document matches" (a lookup in the Documents collection, nothing written) and
# then told the caller to "inspect the CATIA model before retrying; model changes
# were not verified". Read by a caller that trusts the field over the prose, that
# sends a human to check a model nothing touched.
#
# The list is a rule plus named exceptions, not a table of 101 entries: a tool
# called catia_get_*/list_*/measure_* reads, and the extras below are the ones
# whose names do not say so (a sketch reader, a listing under another prefix, and
# the three that only move the camera or the active window). test_v0334 keeps the
# rule honest by checking that no tool in the package ships a writer under one of
# these prefixes without being listed here.
READ_ONLY_PREFIXES = ("catia_get_", "catia_list_", "catia_measure_",
                      "catia_diagnose", "catia_export")

READ_ONLY_EXTRAS = frozenset({
    "catia_sketch_get_geometry",   # reads a sketch's elements
    "catia_gsd_list_elements",     # lists a geometrical set
    "catia_screenshot",            # writes an image file, not the model
    "catia_fit_all",               # camera
    "catia_set_view",              # camera
    "catia_activate_document",     # changes which window is active
})


def is_read_only(tool: str) -> bool:
    """Whether a failure of ``tool`` can leave a half-built model behind."""
    return tool in READ_ONLY_EXTRAS or tool.startswith(READ_ONLY_PREFIXES)


class _Omit:
    """Sentinel: the reply must not carry that key at all."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "NO_RECOVERY"


NO_RECOVERY = _Omit()


def reject_validation(*, tool: str, message: str, code: str,
                      details: list[dict[str, Any]] | None = None,
                      next_actions: list[str] | None = None,
                      data: dict[str, Any] | None = None) -> dict[str, Any]:
    """A rejection that happened BEFORE anything was opened, read or written.

    One shape, because two shapes were the defect: ``catia_run_plan`` answered a
    plan it rejected with a hand-built dict (``ok``, ``code``, ``tool``,
    ``message``, ``data``) that had no ``effects.modified`` and no
    ``failure_stage``, while a plan with NO steps raised a bare ``ValueError``
    that the server turned into ``COM_ERROR`` - complete with "inspect the CATIA
    model before retrying" for a plan that never touched a model (reported
    2026-10-29, report B §5.1).

    A validation failure has three facts and they are always the same three:
    nothing was modified, it failed at the ``validate`` stage, and what to fix is
    an ARGUMENT. Publishing them from one function is what stops the next
    rejection from being written with two of the three.
    """
    payload = operation_result(
        tool=tool,
        ok=False,
        code=code,
        message=message,
        data={"failure_stage": "validate", **(data or {})},
        # Not "unknown": this branch runs before the first COM call, so the
        # model is provably untouched. `None` here is what put "model changes
        # were not verified, inspect the CATIA model" on a rejected plan.
        modified=False,
        recovery=NO_RECOVERY,
        next_actions=next_actions,
    )
    if details:
        payload["data"] = {**payload["data"], "details": details}
    return payload


def operation_result(
    *,
    tool: str,
    ok: bool,
    code: str,
    message: str,
    data: dict[str, Any],
    modified: bool | None,
    operation_id: str | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    rolled_back: bool | None = None,
    recovery: dict[str, Any] | None | _Omit = None,
    next_actions: list[str] | None = None,
) -> dict[str, Any]:
    if rolled_back is True:
        # The tool removed the feature CATIA rejected, so there is nothing left
        # to repair and ``modified`` is not "unknown" - it is a known no.
        modified = False
        actions = [
            "Nothing to repair: the rejected feature was removed again, so the "
            "model is back to its previous state. Fix the argument and retry."
        ]
    elif rolled_back is False:
        actions = [
            "The rejected feature could NOT be removed. Inspect the tree "
            "(catia_list_features) and delete it by hand before anything else "
            "works - a failed feature stops the whole part from updating."
        ]
    elif modified is None:
        actions = ["Inspect the CATIA model before retrying; model changes were not verified"]
    elif not ok and modified:
        actions = ["Inspect partial model changes; recovery is manual"]
    else:
        actions = []
    if next_actions:
        # The raise site knows what to do next; a generic sentence does not. A pad
        # that built no material in either direction is not repaired by "retry
        # with a fixed argument" - retrying IS the failing call (2026-10-28).
        actions = list(next_actions)
    payload: dict[str, Any] = {
        "ok": ok,
        "code": code,
        "operation_id": operation_id or uuid4().hex,
        "tool": tool,
        "message": message,
        "data": data,
        "effects": {"modified": modified},
        "diagnostics": diagnostics or [],
        "warnings": warnings or [],
    }
    if recovery is not NO_RECOVERY:
        block: dict[str, Any] = {
            # What happened, in the order a caller reads it: did this failure
            # undo itself, and what is left to do.
            "rolled_back": rolled_back,
            "automatic_retry_allowed": False,
            "can_request_rollback": False,
            "next_actions": actions,
        }
        if rolled_back is not None:
            # The field that used to sit here was `rollback_supported`, always
            # false - a constant that carried no information and read as a
            # contradiction next to `rolled_back: true` ("不支持回滚" beside "回滚了").
            # Reported 2026-10-28 by both reviewers. A constant describing an
            # input that does not exist is not worth a key; the sentence is worth
            # more than the key was.
            block["rollback_note"] = (
                "no tool in this server takes a rollback argument, so no caller "
                "can ask for one ('can_request_rollback' is always false); "
                "'rolled_back' answers the different question of whether THIS "
                "failure already removed the feature it created")
        if isinstance(recovery, dict):
            block.update(recovery)
        payload["recovery"] = block
    elif actions:
        # A read-only tool has no model to repair, so it publishes no `recovery`
        # block - but some of its failures DO know what to do next, and they are
        # not model repairs: "pass the full path of the copy you mean" is about
        # the ARGUMENT, and DOCUMENT_NAME_AMBIGUOUS is raised almost only by
        # catia_get_* / catia_measure_*. Suppressing the block therefore dropped
        # the two next actions the CHANGELOG promised, on the one code a script
        # branching on fields can never reach (reported 2026-10-28, P2).
        #
        # They travel in `data.next_actions` instead - beside the `candidates`
        # they refer to - so the block keeps its meaning ("what to repair is
        # unknown because nothing was written") while the advice survives.
        payload["data"] = {
            **payload["data"],
            "next_actions": actions,
            "next_actions_note": (
                "this tool does not modify the model, so there is no 'recovery' "
                "block to carry these: they are about the ARGUMENT this call used, "
                "not about a model state to repair"),
        }
    return payload
