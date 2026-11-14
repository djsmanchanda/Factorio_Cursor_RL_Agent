# Path: tests/test_operation_preview.py | Purpose: Protect read-only operation preview evidence.
import copy

import pytest

from orchestrator.mall_bootstrap import MallBootstrapStep, preserve_bootstrap_step
from orchestrator.operation_preview import preview_operation, observe_step_proposal
from orchestrator.operation_session import DeterministicSession

pytestmark = [pytest.mark.deterministic, pytest.mark.fast]


@pytest.mark.parametrize('item,step', [('transport-belt', 'iron-gear-wheel'), ('inserter', 'electronic-circuit')])
def test_loan_preview_preserves_observed_obligation_without_allocating(item, step):
    economics = {'projects': {'other': {'reserved': {'iron-plate': 3}}}, 'loans': [{
        'making': item, 'target': 10, 'lender': 'iron-stick', 'owner': 'project',
        'identity': {'group': 'loan'}, 'step_recipe': step, 'step_required_crafts': 8,
        'step_completed_crafts': 3, 'machine_status': 'working', 'releasing': False}]}
    before = copy.deepcopy(economics)
    menu = {'options': [{'family': 'produce', 'params': {'item': item, 'stock_target': 10}} for _ in range(20)]}
    DeterministicSession.preview_options(None, menu, economics)
    preview = menu['options'][0]['operation_preview']
    assert preview == preview_operation('produce', menu['options'][0]['params'], economics)
    assert preview['active_loan']['remaining_crafts'] == 5
    assert preview['dependency_edges'][0]['to'] == step
    assert preview['immediate_obligation'] is None and 'bill' in preview['unknown']
    assert economics == before
    persisted = MallBootstrapStep(step, 10, 8)
    assert preserve_bootstrap_step(MallBootstrapStep(item, 10, 10), persisted, completed=False) == persisted


def test_unknown_observation_never_claims_zero_or_release():
    preview = preview_operation('production_service', {'item': 'inserter'}, None)
    assert preview['active_loan'] is None and preview['immediate_obligation'] is None
    assert {'active_loan', 'bill', 'release'} <= preview['unknown'].keys()
    assert preview['cycle'] == 'unknown'


@pytest.mark.parametrize('item,prerequisite', [('transport-belt', 'iron-gear-wheel'), ('inserter', 'electronic-circuit')])
def test_shared_step_proposal_matches_actual_configuration_boundary_without_double_allocation(
        tmp_path, monkeypatch, item, prerequisite):
    from types import SimpleNamespace
    from orchestrator import autonomous_builder as ab
    from orchestrator.mall_bootstrap import MallBootstrapLoan
    from orchestrator.material_reservations import MaterialReservationLedger
    from planners.recipe_data import LINE_RECIPES

    monkeypatch.setitem(LINE_RECIPES, item, {'machine': 'assembling-machine-1', 'ingredients': [prerequisite],
                                          'amounts': [1], 'product_amount': 1, 'craft_time': .5})
    monkeypatch.setitem(LINE_RECIPES, prerequisite, {'machine': 'assembling-machine-1', 'ingredients': ['iron-plate'],
                                                  'amounts': [2], 'product_amount': 1, 'craft_time': .5})
    stock = {item: 0, prerequisite: 5, 'iron-plate': 20}
    ledger = MaterialReservationLedger(tmp_path, episode_id='e', surface='nauvis', force='player')
    ledger.declare('other', {prerequisite: 4}, stock)
    before = ledger.path.read_bytes(), copy.deepcopy(ledger.projects), ledger.revision
    monkeypatch.setattr(ab, '_MATERIAL_RESERVATION_LEDGER', ledger)
    monkeypatch.setattr(ab.live_base, 'available_items', lambda *args: dict(stock))
    monkeypatch.setattr(ab.live_base, 'transferable_items', lambda *args: dict(stock))
    monkeypatch.setattr(ab.live_base, 'game_tick', lambda *args: 100)
    monkeypatch.setattr(ab, '_bootstrap_loan_products_finished', lambda *args: 0)
    loan = MallBootstrapLoan('copper-cable', item, 2, 'right', (39.5, 32.5), 'copper-cable')
    client = SimpleNamespace(command=lambda *args: pytest.fail('unexpected command'))
    proposal = observe_step_proposal(client, 'nauvis', 'player', loan)
    fact = {'making': item, 'owner': 'unobserved_claim', 'lender': 'copper-cable',
            'identity': proposal['identity'], 'step_proposal': proposal}
    economics = {'loans': [fact], 'projects': {}}
    for _ in range(20):
        preview = preview_operation('produce', {'item': item, 'stock_target': 2}, economics)
    assert before == (ledger.path.read_bytes(), ledger.projects, ledger.revision)
    assert preview['reservation_owner'] is None
    assert preview['step_proposal']['stage'] == 'before_admission'
    assert preview['ingredient_bill'] == {'kind': 'gross_step_recipe_inputs', 'items': {'iron-plate': 2},
                                         'includes_request_headroom': False, 'allocates_material': False}
    assert preview['immediate_obligation'] is None  # Live admission and release still apply.
    assert proposal['step'] == {'recipe': prerequisite, 'target_count': 6, 'crafts': 1}

    # Exercise the actual shared operation up to its configuration boundary.
    # Four prerequisite items remain owned by the competing project.
    monkeypatch.setattr(ab, 'MANAGED_INTERMEDIATE_SOURCES', {})
    monkeypatch.setattr(ab, '_BLOCKING_MALL_ITEMS', set())
    monkeypatch.setattr(ab, '_missing_chemical_ladder_predecessor', lambda *args: None)
    class AtConfiguration(Exception):
        pass
    configured = []
    def plan(selected_loan, step, **kwargs):
        configured.append(step)
        raise AtConfiguration()
    monkeypatch.setattr(ab, 'bootstrap_loan_plan', plan)
    with pytest.raises(AtConfiguration):
        ab._submit_bootstrap_loan(client, object(), 'nauvis', 'player', loan, lambda *_: None)
    assert configured == [MallBootstrapStep(prerequisite, 6, 1)]
    assert ledger.projects['other'].reserved == {prerequisite: 4}


def test_readonly_allocator_projects_rebalance_without_modifying_live_holds(tmp_path):
    from orchestrator.material_reservations import MaterialReservationLedger
    ledger = MaterialReservationLedger(tmp_path, episode_id='e', surface='nauvis', force='player')
    ledger.declare('other', {'iron-plate': 4}, {'iron-plate': 6})
    before = ledger.path.read_bytes(), copy.deepcopy(ledger.projects), ledger.revision
    assert ledger.preview_allocatable_stock({'iron-plate': 3}, claimant='new') == {'iron-plate': 0}
    assert (ledger.path.read_bytes(), ledger.projects, ledger.revision) == before
    assert ledger.allocatable_stock({'iron-plate': 3}, claimant='new') == {'iron-plate': 0}
    assert ledger.projects['other'].reserved == {'iron-plate': 3}


def test_snapshot_rejects_unknown_progress_and_regressing_clock(monkeypatch):
    from orchestrator import autonomous_builder as ab
    from orchestrator.mall_bootstrap import MallBootstrapLoan
    loan = MallBootstrapLoan('iron-stick', 'transport-belt', 2, 'right', (1, 1), 'iron-gear-wheel',
                             step_recipe='iron-gear-wheel', step_baseline_finished=10, step_required_crafts=2)
    monkeypatch.setattr(ab, '_MATERIAL_RESERVATION_LEDGER', None)
    monkeypatch.setattr(ab.live_base, 'available_items', lambda *args: {'iron-plate': 20})
    monkeypatch.setattr(ab.live_base, 'transferable_items', lambda *args: {})
    monkeypatch.setattr(ab.live_base, 'game_tick', lambda *args: 100)
    monkeypatch.setattr(ab, '_bootstrap_loan_products_finished', lambda *args: None)
    with pytest.raises(ValueError, match='progress observation'):
        observe_step_proposal(object(), 'nauvis', 'player', loan)
    monkeypatch.setattr(ab, '_bootstrap_loan_products_finished', lambda *args: 10)
    ticks = iter([100, 99])
    monkeypatch.setattr(ab.live_base, 'game_tick', lambda *args: next(ticks))
    with pytest.raises(ValueError, match='observation changed'):
        observe_step_proposal(object(), 'nauvis', 'player', loan)
