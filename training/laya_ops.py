# Path: training/laya_ops.py | Purpose: Expose pure operation receipts to training code.
from core.laya_operation_receipts import (
    PRODUCING_OPERATIONS,
    SUBMITTING_OPERATIONS,
    _receipt_result,
    receipt,
)

__all__ = ["PRODUCING_OPERATIONS", "SUBMITTING_OPERATIONS", "receipt"]
