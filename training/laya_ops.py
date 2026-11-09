# Path: training/laya_ops.py | Purpose: Compatibility entry point for shared selected operations.
from orchestrator.operation_session import DeterministicSession as _OperationSession, receipt

__all__ = ["DeterministicSession", "receipt"]


class DeterministicSession(_OperationSession):
    """The shared operation session plus restart-checkpoint state, which lives on the training side."""

    def __init__(self, root, *, origin_episode_id: str | None = None, **kwargs) -> None:
        if origin_episode_id is not None:
            # A restored identity names sidecar paths; reject unsafe values before any session IO.
            from training.laya_restart_state import validate_episode_id
            validate_episode_id(origin_episode_id)
        super().__init__(root, origin_episode_id=origin_episode_id, **kwargs)

    def export_restart_state(self, *, mod_scope: str) -> dict:
        from training.laya_restart_state import export_restart_state
        if not hasattr(self, "_evaluator") or self._baseline is None:
            raise ValueError("Strict acceptance baseline has not been observed")
        return export_restart_state(self.script_output, self.episode_id, mod_scope,
                                    self._baseline, self._history, self._evaluator)

    def restore_restart_acceptance(self, state) -> None:
        from training.laya_restart_state import RestartState
        if (not isinstance(state, RestartState) or state.episode_id != self.episode_id
                or state.script_output != self.script_output.resolve()):
            raise ValueError("Restart state does not match session identity")
        if hasattr(self, "_evaluator"):
            raise ValueError("Cannot replace initialized strict acceptance state")
        self._baseline, self._history, self._evaluator = state.baseline, list(state.history), state.evaluator


def __getattr__(name):
    # Older disposable consumers reference the session's diagnostic helpers here.
    from orchestrator import operation_session
    return getattr(operation_session, name)
