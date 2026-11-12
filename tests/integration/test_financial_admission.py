"""Real financial guards; never patched out, never contacts providers."""
import copy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
import pytest
from cloud_network_benchmark.financial_controls import FinancialError, validate_paid_financial_plan
from cloud_network_benchmark.orchestration import run_lifecycle
from cloud_network_benchmark.admission import BudgetLedger
from tests.financial_helpers import synthetic_finances, rewrite_card, NOW


def test_paid_card_binds_sourced_transfer_allowance(tmp_path):
    book, plans, _, _ = synthetic_finances(tmp_path)
    req, plan = plans[0]
    assert validate_paid_financial_plan(plan, req, scope_id=book.scope_id, now=NOW)['gross_eur'] == '0.56'
    paid_dir = tmp_path/'paid'; paid_dir.mkdir()
    paid_book, paid_plans, _, _ = synthetic_finances(paid_dir, paid=True)
    req,plan = paid_plans[0]
    result = paid_book.reserve(req, plan=plan)
    assert result.status == 'reserved', result.reasons
    transfer = paid_book.inspect()['reservations'][result.evidence.reservation_id.value]['result_transfer']
    assert set(transfer) == {'aws', 'azure', 'gcp'}
    assert all(row['payload_limit_bytes'] == 1000 and row['debited_bytes'] == 0 for row in transfer.values())
    assert book.inspect()['reservations'] == {}


@pytest.mark.parametrize('case', ['missing', 'identity_only', 'expired', 'future', 'wrong_scope', 'wrong_config',
    'runtime_change', 'source_change', 'wrong_count', 'duplicate_entries', 'vm_count', 'zero_gross', 'zero_cash',
    'currency', 'missing_cash', 'stale_price', 'stale_fx', 'missing_fx', 'credit', 'reimbursement', 'project_over300',
    'protected_overrun', 'cash_offset', 'external_missing'])
def test_invalid_authority_never_reserves_or_dispatches(tmp_path, case):
    book, plans, card, path = synthetic_finances(tmp_path, paid=True)
    if case == 'expired': card['valid_until'] = '2000-01-01T00:00:00Z'
    if case == 'future': card['approved_at'] = '2027-01-01T00:00:00Z'
    if case == 'wrong_scope': card['ledger_scope_id'] = 'OTHER'
    if case == 'wrong_config': card['entries'][0]['config_sha256'] = 'e'*64
    if case == 'runtime_change': card['entries'][0]['runtime']['effective_runtime_sha256'] = 'e'*64
    if case == 'source_change': path.with_name('SYNTHETIC-runtime.json').write_text('changed')
    if case == 'wrong_count': card['approved_run_count'] = 2
    if case == 'duplicate_entries': card['entries'] *= 2; card['approved_run_count'] = 2
    if case == 'vm_count': card['entries'][0]['resources']['aws']['vms'] = 0
    if case == 'zero_gross': card['caps']['run_eur'] = '0'
    if case == 'zero_cash': card['caps']['personal_cash_eur'] = '0'
    if case == 'currency': card['currency'] = 'USD'
    if case == 'missing_cash': del card['caps']['personal_cash_eur']
    if case == 'stale_price': card['price']['checked_at'] = '2026-11-08T00:00:00Z'
    if case == 'stale_fx': card['fx']['checked_at'] = '2026-11-08T00:00:00Z'
    if case == 'missing_fx': card.pop('fx')
    if case == 'credit': card['credit_eur'] = '100'
    if case == 'reimbursement': card['funding_treatment'] = 'promised-reimbursement'
    if case == 'project_over300': card['caps']['project_eur'] = '301'
    if case == 'protected_overrun': card['caps']['protected_eur'] = '61'
    if case == 'cash_offset': card['other_gross_eur'] = '10'
    if case == 'external_missing': path.with_name('SYNTHETIC-external.json').unlink()
    plans = rewrite_card(plans, path, card)
    req, plan = plans[0]
    if case == 'missing': plan = replace(plan, prerequisites={})
    if case == 'identity_only': path.write_text('{}'); plans = rewrite_card([(req,plan)], path, {}); req,plan = plans[0]
    before = book.path.read_bytes()
    def forbidden(*_args):
        pytest.fail('financial rejection dispatched provider factory')
    result = run_lifecycle(ledger=book, reservation_request=req, admission_plan=replace(plan, execution_scope='paid_request'),
        allowance=plan.allowance, workflow_factory=forbidden, clock=None)
    assert result.admission_status == 'rejected'
    expected = {
        'missing':'financial_authorization_invalid', 'identity_only':'financial_authorization_invalid',
        'expired':'financial_authorization_expired', 'future':'financial_authorization_expired',
        'wrong_scope':'financial_scope_mismatch', 'wrong_config':'financial_scope_mismatch',
        'runtime_change':'financial_scope_mismatch', 'source_change':'financial_source_changed',
        'wrong_count':'financial_scope_mismatch', 'duplicate_entries':'financial_scope_mismatch',
        'vm_count':'financial_scope_mismatch', 'zero_gross':'financial_run_ceiling_exceeded',
        'zero_cash':'financial_cash_ceiling_exceeded', 'currency':'financial_authorization_invalid',
        'missing_cash':'financial_authorization_invalid', 'stale_price':'financial_quote_stale',
        'stale_fx':'financial_quote_stale', 'missing_fx':'financial_authorization_invalid',
        'credit':'financial_authorization_invalid', 'reimbursement':'financial_authorization_invalid',
        'project_over300':'financial_ceiling_exceeded', 'protected_overrun':'financial_ceiling_exceeded',
        'cash_offset':'financial_external_liabilities_mismatch', 'external_missing':'financial_source_missing'}
    assert result.admission_reasons == (expected[case],)
    assert book.path.read_bytes() == before
    assert not book.inspect()['reservations']
    # Also exercise semantic monetary errors through the same financial ledger
    # path in explicit offline_simulation.


def test_simultaneous_fixed_batch_reservations_have_atomic_shared_ceiling(tmp_path):
    book, plans, card, path = synthetic_finances(tmp_path, count=2)
    # Distinct P0/P1 scopes share the same financial ledger and approval batch.
    req, plan = plans[1]
    campaign = plan.resolved_campaign.model_copy(update={'admission':
        plan.resolved_campaign.admission.model_copy(update={'priority':'P1'})})
    plans[1] = (req.model_copy(update={'priority':'P1'}), replace(plan,resolved_campaign=campaign))
    from cloud_network_benchmark.financial_controls import financial_binding
    card['entries'] = [financial_binding(p,r) for r,p in plans]
    card['caps']['batch_eur'] = '0.60'  # Each0.56 fits; both do not.
    plans = rewrite_card(plans, path, card)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda rp: book.reserve(rp[0], plan=rp[1]), plans))
    assert sorted(r.status for r in results) == ['rejected','reserved']
    assert any(r.reasons == ('financial_batch_id_ceiling_exceeded',) for r in results)
    assert len(book.inspect()['reservations']) == 1


def test_legacy_history_is_never_cleared_to_create_eur_headroom(tmp_path):
    book, plans, _, _ = synthetic_finances(tmp_path, count=2)
    req, plan = plans[0]
    assert book.reserve(req, plan=replace(plan, financial_binding={})).status == 'reserved'
    req, plan = plans[1]
    result = book.reserve(req, plan=plan)
    assert result.reasons == ('financial_legacy_liability_unconverted',)
    assert len(book.inspect()['reservations']) == 1


def test_full_cash_upper_bound_survives_restart_and_cleanup_reconciliation(tmp_path):
    from tests.integration.test_f11_ledger import record_absence
    from cloud_network_benchmark.contracts.common import Provider
    book, plans, _, _ = synthetic_finances(tmp_path)
    req, plan = plans[0]
    decision = book.reserve(req, plan=plan)
    assert decision.status == 'reserved', decision.reasons
    rid = decision.evidence.reservation_id.value
    original = copy.deepcopy(book.inspect()['reservations'][rid]['financial'])
    for provider in Provider:
        record_absence(book, f'SYNTHETIC-{provider}', rid, provider, NOW)
    restarted = BudgetLedger(book.path, scope_id=book.scope_id, utc_now=lambda:NOW+timedelta(days=2),
        monotonic_now=lambda:1., controller_session_id='SYNTHETIC-RESTART')
    assert restarted.inspect()['reservations'][rid]['financial'] == original
    assert Decimal(original['cash_eur']) == Decimal(original['gross_eur'])
    assert restarted.inspect()['reservations'][rid]['cleanup_open'] is False


def test_caps_round_down_before_boundary_comparison(tmp_path):
    book, plans, card, path = synthetic_finances(tmp_path)
    card['caps']['run_eur'] = '0.559999'
    req, plan = rewrite_card(plans,path,card)[0]
    assert book.reserve(req,plan=plan).reasons == ('financial_run_ceiling_exceeded',)


def test_invalid_paid_runtime_does_not_initialize_missing_ledger(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import cloud_network_benchmark.lifecycle_runtime as runtime_module
    book, plans, card, cardpath = synthetic_finances(tmp_path, paid=True)
    card.pop('fx')
    req, plan = rewrite_card(plans, cardpath, card)[0]
    missing = tmp_path/'SYNTHETIC-DO-NOT-INITIALIZE.json'
    runtime = {'providers':dict.fromkeys(plan.child_identities, {}),
               'ledger':{'path':missing, 'scope_id':book.scope_id, 'initialize_if_missing':True}}
    # Constructing exact request inputs is isolated; invalid authority fails
    # before ledger creation and before any provider factory can run.
    monkeypatch.setattr(runtime_module, 'build_admission_request', lambda **_: (req,plan))
    children = {p:SimpleNamespace(execution_state='initialized', mode=plan.resolved_campaign.mode)
                for p in plan.child_identities}
    with pytest.raises(FinancialError, match='financial_authorization_invalid'):
        runtime_module.build_runtime(campaign=plan.resolved_campaign, parent=None, children=children,
            costs=plan.costs, runtime=runtime, controller_session_id='SYNTHETIC', runner=None,
            clock=SimpleNamespace(utc_now=lambda:NOW, monotonic=lambda:1.), private_root=tmp_path/'private')
    assert not missing.exists() and not missing.with_suffix('.json.lock').exists()


@pytest.mark.parametrize('limit,reason', [('personal_cash_eur','financial_cash_ceiling_exceeded'),
    ('working_eur','financial_project_ceiling_exceeded'), ('stage_eur','financial_stage_id_ceiling_exceeded'),
    ('provider','financial_provider_ceiling_exceeded')])
def test_shared_independent_ceilings_cannot_be_spent_twice(tmp_path, limit, reason):
    book, plans, card, path = synthetic_finances(tmp_path, count=2)
    if limit == 'provider':
        card['caps']['providers_eur']['aws'] = '0.25'
    else:
        card['caps'][limit] = '0.60' if limit == 'stage_eur' else '1.08'
    plans = rewrite_card(plans,path,card)
    first = book.reserve(plans[0][0], plan=plans[0][1])
    assert first.status == 'reserved', first.reasons
    second = book.reserve(plans[1][0], plan=plans[1][1])
    assert second.reasons == (reason,)
    assert len(book.inspect()['reservations']) == 1


def test_expired_authority_stops_new_stages_but_original_cleanup_reconciles(tmp_path):
    from cloud_network_benchmark.financial_controls import revalidate_financial_record
    from cloud_network_benchmark.contracts.common import Provider
    from tests.integration.test_f11_ledger import record_absence
    book, plans, _, cardpath = synthetic_finances(tmp_path)
    req, plan = plans[0]
    decision = book.reserve(req,plan=plan)
    rid = decision.evidence.reservation_id.value
    state = book.inspect()
    record = state['reservations'][rid]
    # Synthetic record models an original paid reservation; no provider was launched.
    record['execution_scope'] = 'paid_request'
    with pytest.raises(FinancialError, match='financial_authorization_expired'):
        revalidate_financial_record(record, state=state, now=NOW+timedelta(days=2), end=NOW+timedelta(days=2, hours=1))
    # Cleanup/reconciliation requires neither this card nor the original quote files.
    cardpath.unlink()
    for provider in Provider:
        record_absence(book, f'SYNTHETIC-expired-{provider}', rid, provider, NOW+timedelta(days=2))
    assert book.inspect()['reservations'][rid]['cleanup_open'] is False
    assert book.inspect()['reservations'][rid]['financial']['gross_eur'] == '0.56'


def test_higher_reviewed_fx_revalues_prior_unbilled_cash_without_erasing_margin(tmp_path):
    import json
    from tests.financial_helpers import save
    book, plans, card, path = synthetic_finances(tmp_path, count=2)
    card['caps']['personal_cash_eur'] = '1.80'
    plans = rewrite_card(plans,path,card)
    assert book.reserve(plans[0][0],plan=plans[0][1]).status == 'reserved'
    fxfile = tmp_path/'SYNTHETIC-fx-higher.json'
    fxdata = json.loads((tmp_path/'SYNTHETIC-fx.json').read_text())
    fxdata['conservative_eur_per_unit'] = '2.00'
    card['fx'] = {**fxdata, **save(fxfile,fxdata)}
    updated = rewrite_card(plans,tmp_path/'SYNTHETIC-reapproved-higher-fx.json',card)
    result = book.reserve(updated[1][0],plan=updated[1][1])
    assert result.reasons == ('financial_cash_ceiling_exceeded',)
    assert len(book.inspect()['reservations']) == 1
