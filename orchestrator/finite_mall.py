# Path: orchestrator/finite_mall.py | Purpose: Expose finite bill production through existing owned mall loans, without allocating cells.
"""An explicit reuse operation; standing-producer and donor policies stay separate."""
from orchestrator import autonomous_builder as ab
from orchestrator.mall_construction import observe as pending_cells, resume_for_item


def observe(session, item, stock_target):
    if type(stock_target) is not int or stock_target < 1:
        raise ValueError('finite batch needs a positive absolute stock target')
    spec = ab.LINE_RECIPES.get(item) or {}
    supported = (item in ab.RATIONED_MALL_BATCH_ITEMS and item not in ab._MALL_RECIPE_ANCHORS
                 and spec.get('machine') in ab.live_base.ASSEMBLER_TIERS
                 and not spec.get('fluid_ingredients'))
    facts = {'item': item, 'stock_target': stock_target, 'supported': supported,
             'known': False, 'eligible': False, 'reason': 'unsupported_item',
             'candidates': [], 'active_loans': [], 'selected': None}
    if not supported:
        facts['known'] = True
        return facts
    try:
        session.invocations.require_clear()
        c, ref = session.client, session.reference_point
        pending = pending_cells(c, session.invocations, ref, item=item)
        facts['pending_owned_configuration'] = pending
        if pending:
            facts.update(known=True, reason='pending_owned_configuration')
            return facts
        if ab._independent_mall_ready(c, 'nauvis', 'player'):
            facts.update(known=True, reason='independent_mall')
            return facts
        # Admission is context, never permission to create a cell in this operation.
        facts['slot_cap'] = ab._bootstrap_mall_slot_limit(c, 'nauvis', 'player')
        facts['occupied_slots'] = ab.mall_slot_count(c, 'nauvis', ref)
        facts['standing_persistent_item'] = item in ab.PERSISTENT_INTERMEDIATES
        if (ab._recipe_depends_on_fast_transport_belt(item)
                and not ab._fast_transport_belt_gate_open(c, 'nauvis', 'player')):
            facts.update(known=True, reason='fast_belt_capability_closed')
            return facts
        ladder = ab._unfunded_ladder_ingredient(c, 'nauvis', 'player', item, stock_target)
        if ladder is not None:
            facts.update(known=True, reason='chemical_capability_wait', prerequisite=ladder)
            return facts
        stock = ab._transferable_or_available_stock(c, 'nauvis', 'player')
        facts['available_stock'] = stock.get(item, 0)
        if facts['available_stock'] >= stock_target:
            facts.update(known=True, eligible=True, reason='stock_satisfied')
            return facts
        loans = ab.active_bootstrap_loans(c, 'nauvis', 'player')
        facts['active_loans'] = [{'item': loan.target_item, 'position': list(loan.machine_position),
                                  'original_recipe': loan.original_recipe} for loan in loans]
        if any(loan.target_item == item for loan in loans):
            facts.update(known=True, eligible=True, reason='active_requested_batch')
            return facts
        selected = ab._borrow_free_mall_cell(c, 'nauvis', 'player', item, ref,
            allowed_original_recipes=None, allow_shared_provider=False, require_stocked_original=False,
            excluded_origins={ab._loan_cell_origin(loan) for loan in loans},
            observations=facts['candidates'])
        if selected is not None:
            recipe, position, origin, side, machine = selected
            facts['selected'] = {'original_recipe': recipe, 'position': list(position),
                                 'origin': list(origin), 'side': side, 'machine': machine}
        facts.update(known=True, eligible=selected is not None or bool(loans),
                     reason='existing_half' if selected is not None else
                            'active_loan_handoff' if loans else 'no_legal_half')
    except Exception as error:
        facts.update(known=False, eligible=False, reason='observation_unknown',
                     error=f'{type(error).__name__}: {error}')
    return facts


def execute(session, *, item, stock_target):
    facts = observe(session, item, stock_target)
    if not facts['supported'] or not facts['known'] or not facts['eligible']:
        raise ab.ProductionPrerequisiteDeferred('finite bill batch is unavailable: ' + facts['reason'],
            code='finite_batch_unavailable', state='supply_wait', details=facts)
    if facts['reason'] == 'stock_satisfied':
        return {'ok': True, 'code': 'stock_satisfied', 'state': 'ready', 'details': facts}
    # Preserve the selected-owned construction fence before any new allocation
    # or loan. The shared operation re-observes candidate legality itself.
    resume_for_item(session.client, session.bridge, 'nauvis', 'player', item,
                    session.reference_point, session.emit)
    active = ab._rationed_mall_batch(session.client, session.bridge, 'nauvis', 'player', item,
        stock_target, session.reference_point, session.emit, reuse_only=True)
    raise ab.ProductionPrerequisiteDeferred('finite bill production ' + ('advanced' if active else 'needs re-observation'),
        code='temporary_mall_batch' if active else 'finite_batch_unavailable',
        state='supply_wait', details=facts)
