# Path: core/laya_operation_receipts.py | Purpose: Describe deterministic operation outcomes without runtime dependencies.
import json


PRODUCING_OPERATIONS = {"produce", "mall_slot", "steel", "grow"}
SUBMITTING_OPERATIONS = {"starter", "foundation", "extend"}


def receipt(operation: str, params: dict, result: object) -> dict:
    """Report success only when the operation's own postcondition holds."""
    if operation in PRODUCING_OPERATIONS:
        code = "producing" if result is not None else "prerequisite_advanced"
    elif operation in SUBMITTING_OPERATIONS:
        code = "submitted"
    elif operation == "reserve_steel_seed":
        code = "reserved"
    else:
        code = "done"
    return {"ok": code != "prerequisite_advanced", "operation": operation, "params": params, "code": code,
            "result": _receipt_result(result)}


def _receipt_result(result: object) -> object:
    if isinstance(result, dict):
        try:
            if len(json.dumps(result)) <= 200:
                return result
        except (TypeError, ValueError):
            pass
    return None if result is None else str(result)[:200]
