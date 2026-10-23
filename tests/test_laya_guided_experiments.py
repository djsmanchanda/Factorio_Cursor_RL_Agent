# Path: tests/test_laya_guided_experiments.py | Purpose: Verify opt-in guided sampling while freezing default probabilities and RNG choices.

import copy
import json
import math
from collections import Counter

import pytest

from training.laya_guided_policy import GuidedPolicy
from training.laya_ops_runner import brief, compile_options

pytestmark = [pytest.mark.training, pytest.mark.fast]


def menu():
    return {"needs": [], "options": [
        {"option_id": "wait.300", "family": "wait", "need_id": "wait"},
        {"option_id": "op.starter.iron-plate", "family": "starter", "need_id": "item:iron-plate"},
        {"option_id": "op.starter.copper-plate", "family": "starter", "need_id": "item:copper-plate"},
        {"option_id": "op.produce.electronic-circuit", "family": "produce", "need_id": "stock:electronic-circuit"}]}


PREFS = {"test": [{"when": {"family": "wait"}, "weight": 0.25},
                  {"when": {"need": "item:iron-plate"}, "weight": 2},
                  {"when": {"need": "item:iron-plate"}, "weight": 2},
                  {"when": {"family": "produce"}, "weight": 2.5}]}
STATE = {"guidelines": [{"rule_id": "test"}]}


@pytest.mark.parametrize("options", [{}, {"temperature": 1, "exploration_mode": "options", "novelty_strength": 0}])
def test_defaults_keep_original_probability_bits_row_shape_and_rng(options):
    # Captured from 9ac7937 before changing the policy; exact floats, not an approximate oracle.
    policy = GuidedPolicy(7, preferences=PREFS, **options)
    before = policy.rng.getstate()
    rows = policy.distribution(STATE, menu())
    assert policy.rng.getstate() == before
    assert [r["weight"] for r in rows] == [0.25, 4.0, 1.0, 2.5]
    assert [r["probability"].hex() for r in rows] == [
        "0x1.09e8e09e8e09ep-4", "0x1.e7a3827a3827ap-2", "0x1.2d6b5ad6b5ad7p-3", "0x1.3f2c97f2c97f2p-2"]
    assert all(set(r) == {"option_id", "need_id", "weight", "rules", "features", "probability"} for r in rows)
    ids = [o["option_id"] for o in menu()["options"]]
    assert [policy.choose(STATE, menu())["option_id"] for _ in range(20)] == [
        ids[i] for i in [1, 1, 2, 1, 1, 1, 0, 1, 0, 1, 1, 1, 1, 3, 1, 1, 2, 3, 2, 1]]


def test_family_exploration_mass_is_independent_of_menu_width():
    m = menu()
    m["options"] += [{"option_id": f"op.produce.extra-{i}", "family": "produce", "need_id": "stock:extra"}
                     for i in range(8)]
    rows = GuidedPolicy(4, epsilon=1, preferences={}, exploration_mode="families").distribution({}, m)
    mass = Counter()
    for row, option in zip(rows, m["options"]):
        mass[option["family"]] += row["probability"]
    assert dict(mass) == pytest.approx({"wait": 1 / 3, "starter": 1 / 3, "produce": 1 / 3})
    assert rows[1]["probability"] == rows[2]["probability"] == 1 / 6
    assert rows[-1]["probability"] == pytest.approx(1 / 27)


def test_family_mode_changes_only_the_exploration_component():
    rows = GuidedPolicy(4, epsilon=0.2, preferences=PREFS, exploration_mode="families").distribution(STATE, menu())
    for row, raw in zip(rows, [0.25, 4, 1, 2.5]):
        assert row["exploitation_probability"] == pytest.approx(raw / 7.75)
        assert row["probability"] == pytest.approx(0.8 * raw / 7.75 + 0.2 * row["exploration_probability"])


@pytest.mark.parametrize("temperature", [0.5, 2.0, 100.0])
def test_temperature_applies_to_the_full_product_of_visible_priors(temperature):
    rows = GuidedPolicy(3, preferences=PREFS, temperature=temperature).distribution(STATE, menu())
    weights = [w ** (1 / temperature) for w in [0.25, 4, 1, 2.5]]
    for row, expected in zip(rows, weights):
        assert row["probability"] == pytest.approx(0.85 * expected / sum(weights) + 0.15 / 4)
    assert rows[1]["rules"] == ["test", "test"]
    assert rows[1]["factors"]["priors"] == [{"rule_id": "test", "weight": 2.0}] * 2
    assert rows[1]["prior_log_weight"] == pytest.approx(math.log(4))
    hidden = GuidedPolicy(3, preferences=PREFS, temperature=temperature).distribution({}, menu())
    assert all(row["probability"] == 0.25 for row in hidden)


def test_temperature_softens_and_strengthens_rather_than_reversing_preferences():
    def iron_probability(temperature):
        return GuidedPolicy(3, preferences=PREFS, temperature=temperature).distribution(STATE, menu())[1]["probability"]
    assert iron_probability(0.5) > iron_probability(1) > iron_probability(2) > 0.25


def ops_state_and_menu():
    # Real compile_options/history_features shape: attempts stay in the runner, binary facts reach the policy.
    results = [{"operation": "starter", "params": {"recipe": "iron-plate"}, "ok": False,
                "code": "coverage_wait", "tick": 0}]
    board = {"needs": [{"item": item, "condition": "short", "goal_required_per_s": 1}
                       for item in ["iron-plate", "copper-plate", "stone-brick"]]}
    m = compile_options(board, results, tick=600)
    s = {"brief": brief(results, None, None, {}, 0), "guidelines": [],
         "observation_digest": {"mission_phase": "C0", "factory_status": {}}}
    return s, m


def test_binary_novelty_changes_real_ops_without_history_rule_visibility_or_hidden_counters():
    s, m = ops_state_and_menu()
    original = copy.deepcopy((s, m))
    policy = GuidedPolicy(9, preferences={}, novelty_strength=3)
    rows = {r["option_id"]: r for r in policy.distribution(s, m)}
    iron, copper, wait = [rows[k] for k in ["op.starter.iron-plate", "op.starter.copper-plate", "wait.300"]]
    assert iron["features"]["tried"] is True and copper["features"]["tried"] is False
    assert iron["factors"]["novelty_multiplier"] == wait["factors"]["novelty_multiplier"] == 1
    assert copper["factors"]["novelty_multiplier"] == 4
    assert copper["probability"] > iron["probability"] == wait["probability"]
    assert wait["factors"]["tried"] is None and "tried" not in wait["features"]
    assert copper["factors"]["novelty_source"] == "options.features.tried"
    baseline = GuidedPolicy(9, preferences={}).distribution(s, m)
    assert all(r["probability"] == baseline[0]["probability"] for r in baseline)
    for _ in range(5):
        policy.choose(s, m)
    assert policy.distribution(s, m) == list(rows.values())
    assert (s, m) == original


def test_novelty_rewards_exact_untried_options_within_one_family():
    m = menu()
    m["options"][1]["features"] = {"tried": True}
    m["options"][2]["features"] = {"tried": False}
    rows = GuidedPolicy(3, preferences={}, novelty_strength=1).distribution({}, m)
    assert rows[2]["exploitation_probability"] == 2 * rows[1]["exploitation_probability"]
    # More failure/progress facts do not increase or reduce the binary novelty factor.
    m["options"][1]["features"].update(repeat_fails=3, after_success_same_family=True, stuck=True, no_progress=5)
    again = GuidedPolicy(3, preferences={}, novelty_strength=1).distribution({}, m)
    assert [r["probability"] for r in again] == [r["probability"] for r in rows]


@pytest.mark.parametrize("unknown", [None, 0, 1, "false"])
def test_unknown_or_nonboolean_history_is_neutral_and_does_not_create_outcomes(unknown):
    m = menu()
    for o in m["options"]:
        o["features"] = {"tried": unknown}
    rows = GuidedPolicy(3, preferences={}, novelty_strength=10).distribution({"brief": "RECENT: none yet"}, m)
    assert all(r["probability"] == 0.25 and r["factors"]["tried"] is None for r in rows)
    assert all(r["factors"]["novelty_multiplier"] == 1 for r in rows)
    assert all("repeat_fails" not in r["features"] and "after_success_same_family" not in r["features"] for r in rows)


def test_temperature_and_novelty_factors_have_explicit_separate_effects():
    m = menu()
    m["options"][2]["features"] = {"tried": False}
    rows = GuidedPolicy(3, preferences=PREFS, temperature=2, novelty_strength=3).distribution(STATE, m)
    effective = [0.5, 2, 4, math.sqrt(2.5)]
    assert [r["exploitation_probability"] for r in rows] == pytest.approx([w / sum(effective) for w in effective])


@pytest.mark.parametrize("weights", [[1e308, 1e308], [1e-300, 1e-300],
                                     [1e308, 1e308, 1e-308, 1e-308], [1e-308, 1e-308, 1e308, 1e308]])
@pytest.mark.parametrize("temperature", [1.0, 2.0, 5e-324, 1e308])
def test_extreme_products_and_temperatures_stay_finite_and_keep_legal_support(weights, temperature):
    preferences = {"test": [{"when": {"family": "starter"}, "weight": w} for w in weights]}
    policy = GuidedPolicy(11, preferences=preferences, temperature=temperature)
    rows = policy.distribution(STATE, menu())
    assert math.fsum(r["probability"] for r in rows) == pytest.approx(1)
    assert all(math.isfinite(r["weight"]) and r["probability"] >= 0.15 / 4 for r in rows)
    json.dumps(policy.choose(STATE, menu()), allow_nan=False)
    if len(weights) == 4:
        assert [r["probability"] for r in rows] == pytest.approx([0.25] * 4)


def test_extreme_novelty_and_subnormal_epsilon_keep_positive_support_and_serializable_logs():
    m = menu()
    m["options"][1]["features"] = {"tried": False}
    policy = GuidedPolicy(8, epsilon=5e-324, preferences=PREFS, temperature=5e-324,
                          novelty_strength=float.fromhex("0x1.fffffffffffffp+1023"))
    rows = policy.distribution(STATE, m)
    assert all(r["probability"] > 0 for r in rows)
    assert math.fsum(r["probability"] for r in rows) == 1
    assert all(r["weight_basis"] == "max_scaled_effective" for r in rows)
    json.dumps(policy.choose(STATE, m), allow_nan=False)


@pytest.mark.parametrize("options", [
    {"temperature": 0}, {"temperature": -1}, {"temperature": float("nan")}, {"temperature": float("inf")},
    {"temperature": "2"}, {"novelty_strength": -1}, {"novelty_strength": float("nan")},
    {"novelty_strength": float("inf")}, {"novelty_strength": True},
    {"exploration_mode": "family"}, {"exploration_mode": None},
    {"epsilon": 0}, {"epsilon": 1.1}, {"epsilon": float("nan")}, {"epsilon": float("inf")}])
def test_invalid_configuration_is_rejected(options):
    with pytest.raises(ValueError):
        GuidedPolicy(1, preferences={}, **options)


@pytest.mark.parametrize("weight", [-1, float("nan"), float("inf")])
def test_invalid_prior_weights_cannot_destroy_the_distribution(weight):
    with pytest.raises(ValueError, match="preference weight"):
        GuidedPolicy(1, preferences={"test": [{"when": {}, "weight": weight}]})


def test_zero_priors_keep_default_bits_and_exploration_support_in_experiments():
    prefs = {"test": [{"when": {"family": "starter"}, "weight": 0}]}
    default = GuidedPolicy(1, preferences=prefs).distribution(STATE, menu())
    assert [r["probability"] for r in default] == [0.46249999999999997, 0.0375, 0.0375, 0.46249999999999997]
    assert all("factors" not in r for r in default)
    rows = GuidedPolicy(1, preferences=prefs, temperature=2).distribution(STATE, menu())
    assert rows[1]["exploitation_probability"] == rows[2]["exploitation_probability"] == 0
    assert rows[1]["probability"] == rows[2]["probability"] == 0.15 / 4
    assert rows[1]["prior_log_weight"] is None
    json.dumps(rows, allow_nan=False)
    with pytest.raises(ValueError, match="positive prior weight"):
        GuidedPolicy(1, preferences={"test": [{"when": {}, "weight": 0}]}).distribution(STATE, menu())


@pytest.mark.parametrize("options", [{"temperature": 3}, {"exploration_mode": "families"},
                                    {"novelty_strength": 4},
                                    {"temperature": 0.5, "exploration_mode": "families", "novelty_strength": 2}])
def test_modes_are_reproducible_sample_only_legal_options_and_keep_every_option_supported(options):
    s, m = ops_state_and_menu()
    a, b = [GuidedPolicy(9, preferences={}, **options) for _ in range(2)]
    rows = a.distribution(s, m)
    assert [r["option_id"] for r in rows] == [o["option_id"] for o in m["options"]]
    assert all(r["probability"] > 0 for r in rows)
    choices = [a.choose(s, m) for _ in range(30)]
    assert choices == [b.choose(s, m) for _ in range(30)]
    assert len({c["option_id"] for c in choices}) > 1
    assert all(c["option_id"] in {o["option_id"] for o in m["options"]} for c in choices)


def test_single_legal_option_and_empty_menu():
    m = {"needs": [], "options": [menu()["options"][0]]}
    policy = GuidedPolicy(4, preferences={}, temperature=3, exploration_mode="families", novelty_strength=2)
    assert policy.choose({}, m)["probability"] == 1
    with pytest.raises(ValueError, match="legal option"):
        policy.distribution({}, {"needs": [], "options": []})
