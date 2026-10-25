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
    ``recovery.rollback_supported`` was hard-coded false and ``next_actions``
    said "recovery is manual" even when the tool had just undone the change.
    """

    def __init__(self, message: str, rolled_back: bool) -> None:
        super().__init__(message)
        self.rolled_back = bool(rolled_back)


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
) -> dict[str, Any]:
    if rolled_back is True:
        # The tool removed the feature CATIA rejected, so there is nothing left
        # to repair and ``modified`` is not "unknown" - it is a known no.
        modified = False
        next_actions = [
            "Nothing to repair: the rejected feature was removed again, so the "
            "model is back to its previous state. Fix the argument and retry."
        ]
    elif rolled_back is False:
        next_actions = [
            "The rejected feature could NOT be removed. Inspect the tree "
            "(catia_list_features) and delete it by hand before anything else "
            "works - a failed feature stops the whole part from updating."
        ]
    elif modified is None:
        next_actions = ["Inspect the CATIA model before retrying; model changes were not verified"]
    elif not ok and modified:
        next_actions = ["Inspect partial model changes; recovery is manual"]
    else:
        next_actions = []
    return {
        "ok": ok,
        "code": code,
        "operation_id": operation_id or uuid4().hex,
        "tool": tool,
        "message": message,
        "data": data,
        "effects": {"modified": modified},
        "diagnostics": diagnostics or [],
        "warnings": warnings or [],
        "recovery": {
            # No separate rollback request exists: a client cannot ask for one,
            # so this stays false. Whether *this* failure already undid itself is
            # a different question, and ``rolled_back`` is the field that
            # answers it.
            "rollback_supported": False,
            "rolled_back": rolled_back,
            "automatic_retry_allowed": False,
            "next_actions": next_actions,
        },
    }
