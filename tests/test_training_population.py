# Path: tests/test_training_population.py
# Purpose: Verify seeded elitist policy evolution without crossover.

from __future__ import annotations

from training.population import PolicyGenome, evolve_population


def test_population_retains_elite_and_mutates_seeded_children() -> None:
    population = [
        PolicyGenome("weak", 0, {"alpha": 1.0, "regularization": 1.0}),
        PolicyGenome("strong", 0, {"alpha": 0.5, "regularization": 2.0}),
    ]
    kwargs = dict(
        fitness_by_id={"weak": (0, 0.5), "strong": (0, 0.9)},
        generation=1, seed=7, size=4, elite_count=1, mutation_scale=0.2,
    )
    first = evolve_population(population, **kwargs)
    second = evolve_population(population, **kwargs)

    assert first == second
    assert first[0] == population[1]
    assert all(child.parent_policy_id == "strong" for child in first[1:])
    assert len({child.policy_id for child in first}) == 4


def test_population_rejects_missing_fitness() -> None:
    genome = PolicyGenome("p", 0, {"alpha": 1.0})
    try:
        evolve_population(
            [genome], {}, generation=1, seed=1, size=2,
            elite_count=1, mutation_scale=0.1,
        )
    except ValueError as error:
        assert "missing fitness" in str(error)
    else:
        raise AssertionError("missing fitness was accepted")
