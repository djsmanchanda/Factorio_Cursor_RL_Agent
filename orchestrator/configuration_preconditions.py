# Path: orchestrator/configuration_preconditions.py | Purpose: Probe executor configuration guards without mutation.
"""Only an explicit versioned capability response authorizes guarded plans."""

import json


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate capability key')
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError('non-JSON numeric constant: ' + value)


def supports_configuration_preconditions(client) -> bool | None:
    """Return v1 support, explicit lack of support, or unknown on probe failure."""
    try:
        raw = client.command('/layout_executor_capabilities')
        response = json.loads(raw, object_pairs_hook=_unique_object,
                              parse_constant=_reject_constant)
    except Exception:
        return None
    if not isinstance(response, dict):
        return None
    version = response.get('configuration_preconditions')
    if type(version) is not int or version < 0:
        return None
    return version == 1
