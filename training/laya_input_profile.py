# Path: training/laya_input_profile.py | Purpose: Track observation representations separately from token sequence syntax.

from collections.abc import Mapping

from training.laya_factory_health import VERSION as HEALTH_VERSION

STATE_PROFILE = 'state-v1'
CORE_PROFILE = 'workflow-core-v1'
HEALTH_PROFILE = 'workflow-health-v1'
SAVED_WORLD_PROFILE = 'saved-world-v1'
MALL_PROFILE = 'mall-world-v1'
INPUT_PROFILES = frozenset({STATE_PROFILE, CORE_PROFILE, HEALTH_PROFILE, SAVED_WORLD_PROFILE, MALL_PROFILE})


def input_profile(state: Mapping) -> str:
    observation = state.get('observation_digest') or {}
    if not isinstance(observation, Mapping):
        raise ValueError('observation digest must be a mapping')
    if 'mall' in observation:
        mall = observation['mall']
        if not isinstance(mall, Mapping) or mall.get('version') != MALL_PROFILE:
            raise ValueError('unsupported mall observation representation')
        if any(key in observation for key in ('saved_world', 'factory_health', 'work_branches', 'item_supply')):
            raise ValueError('mall facts cannot be disguised as other inputs')
        return MALL_PROFILE
    if 'saved_world' in observation:
        saved = observation['saved_world']
        if not isinstance(saved, Mapping) or saved.get('version') != SAVED_WORLD_PROFILE:
            raise ValueError('unsupported saved-world observation representation')
        if 'factory_health' in observation or 'work_branches' in observation or 'item_supply' in observation:
            raise ValueError('saved-world facts cannot be disguised as workflow inputs')
        return SAVED_WORLD_PROFILE
    health = observation.get('factory_health')
    if health is not None:
        if not isinstance(health, Mapping) or health.get('version') != HEALTH_VERSION:
            raise ValueError('unsupported factory health observation representation')
        return HEALTH_PROFILE
    if 'work_branches' in observation or 'item_supply' in observation:
        return CORE_PROFILE
    return STATE_PROFILE


def require_input_profile(config: Mapping, state: Mapping, *, allow_unrecorded: bool = False) -> str:
    profile = input_profile(state)
    supported = config.get('input_profiles')
    if supported is None:
        if profile in {SAVED_WORLD_PROFILE, MALL_PROFILE}:
            raise ValueError(f'checkpoint has no recorded {profile} training; retrain for this domain')
        # Preserved v2 adapters predate health telemetry. They may still be
        # diagnosed explicitly, but must not silently receive the new facts.
        if profile == HEALTH_PROFILE and not allow_unrecorded:
            raise ValueError('checkpoint has no recorded workflow-health-v1 training; '
                             'retrain or use an explicit unrecorded-profile diagnostic')
        return profile
    if (not isinstance(supported, list) or not supported
            or any(not isinstance(value, str) or value not in INPUT_PROFILES for value in supported)):
        raise ValueError('checkpoint has invalid input_profiles metadata')
    if profile not in supported:
        raise ValueError(f'checkpoint was not trained for input profile {profile!r}')
    return profile
