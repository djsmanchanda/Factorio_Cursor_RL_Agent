# Path: tests/test_resource_patch_cache.py
# Purpose: Keep expensive contiguous-patch floods run-scoped and invalidatable.

from orchestrator import resource_patches


class _Client:
    def __init__(self) -> None:
        self.calls = 0

    def command(self, _command: str) -> str:
        self.calls += 1
        return "10.5 20.5 8.5 18.5 12.5 22.5 250000"


def test_identical_patch_surveys_are_cached_until_invalidation() -> None:
    client = _Client()
    resource_patches.clear_patch_cache()

    first = resource_patches.nearest_patch(
        client, "nauvis", "copper-ore", (0.0, 0.0),
    )
    second = resource_patches.nearest_patch(
        client, "nauvis", "copper-ore", (0.0, 0.0),
    )

    assert first == second
    assert client.calls == 1

    resource_patches.invalidate_patch_cache(client, "nauvis", "copper-ore")
    resource_patches.nearest_patch(
        client, "nauvis", "copper-ore", (0.0, 0.0),
    )
    assert client.calls == 2
