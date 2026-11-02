"""The workflow states and the only moves allowed between them."""
INTAKE, SEARCH, PICK, HOLD = "INTAKE", "SEARCH", "PICK", "HOLD"
AWAIT_APPROVAL, EXECUTE, RECONCILE, DONE, FAILED = "AWAIT_APPROVAL", "EXECUTE", "RECONCILE", "DONE", "FAILED"

ALLOWED: dict[str, set[str]] = {
    INTAKE: {SEARCH, PICK, AWAIT_APPROVAL, FAILED},
    SEARCH: {PICK, INTAKE, HOLD, AWAIT_APPROVAL, FAILED},
    PICK: {HOLD, SEARCH, INTAKE, AWAIT_APPROVAL, FAILED},
    HOLD: {AWAIT_APPROVAL, PICK, SEARCH, INTAKE, FAILED},
    AWAIT_APPROVAL: {EXECUTE, HOLD, PICK, SEARCH, INTAKE, AWAIT_APPROVAL, FAILED},
    EXECUTE: {RECONCILE, DONE, SEARCH, FAILED},
    RECONCILE: {DONE, EXECUTE, SEARCH, FAILED},
    DONE: {INTAKE},
    FAILED: {INTAKE},
}


class InvalidTransition(Exception):
    pass


def check(frm: str, to: str) -> None:
    if to not in ALLOWED[frm]:
        raise InvalidTransition(f"{frm} -> {to}")
