"""Synthetic, non-authorizing financial policy regressions."""
from decimal import Decimal
import pytest
from cloud_network_benchmark.financial_controls import (liability, ceiling, FinancialAuthorization,
    has_explicit_eur_reserve_release, legacy_usd_budget_exceeded, validate_paid_financial_plan)

@pytest.mark.parametrize('raw,expected', [('0.0001','0.01'), ('1.001','1.01'), ('1.00','1.00')])
def test_liability_rounds_up(raw, expected):
    assert liability(raw) == Decimal(expected)

@pytest.mark.parametrize('raw', [True, False, '-1', 'NaN', 'Infinity', None])
def test_invalid_amounts_fail_closed(raw):
    with pytest.raises(ValueError):
        liability(raw)

def test_limit_never_rounds_up():
    assert ceiling('1.009') == Decimal('1.00')

def test_generic_or_offset_approval_is_not_authority():
    with pytest.raises(ValueError):
        FinancialAuthorization.model_validate({'evidence_id':'SYNTHETIC', 'credit_eur':'100'})


def test_price_age_requires_hours_not_calendar_date(tmp_path):
    from datetime import timedelta
    from cloud_network_benchmark.financial_controls import CheckedSource, FinancialError
    from tests.financial_helpers import save, NOW
    source = save(tmp_path/'SYNTHETIC.json', {})
    quote = CheckedSource(**source, official_urls=['https://example.invalid/SYNTHETIC'],
        checked_at=NOW-timedelta(hours=24), valid_until=NOW+timedelta(hours=1))
    quote.check(NOW, NOW+timedelta(minutes=20))
    with pytest.raises(FinancialError, match='financial_quote_stale'):
        quote.check(NOW+timedelta(seconds=1), NOW+timedelta(minutes=20))
    with pytest.raises(FinancialError, match='financial_quote_stale'):
        quote.check(NOW-timedelta(days=2), NOW)


def test_eur_conversion_covers_the_usd_amount_f11_actually_reserves():
    from cloud_network_benchmark.financial_controls import provider_liability
    # Source dollars round up to0.0100 in F11. At1.01EUR/USD that is0.0101EUR,
    # so reserving only one EUR cent would understate the stored USD liability.
    assert provider_liability({'compute':{'amount_usd':'0.00990099'}},
                              Decimal('1.01'), Decimal('0')) == Decimal('0.02')


def _synthetic_approval_snapshot(tmp_path, *, release_reserve=True):
    from tests.financial_helpers import NOW, rewrite_card, synthetic_finances
    book, plans, card, path = synthetic_finances(tmp_path, paid=True)
    if release_reserve:
        card['caps'].update(project_eur='300', working_eur='264', protected_eur='36',
                            personal_cash_eur='264', batch_eur='264', stage_eur='264')
    else:
        card['caps'].update(project_eur='250', working_eur='180', protected_eur='50',
                            personal_cash_eur='180', batch_eur='180', stage_eur='180')
    req, plan = rewrite_card(plans, path, card)[0]
    snapshot = validate_paid_financial_plan(plan, req, scope_id=book.scope_id, now=NOW)
    return book, req, plan, card, path, snapshot, NOW


def test_eur_reserve_release_requires_source_card_caps_and_reproduces_usd_rejection(tmp_path):
    _book, _req, _plan, _card, _path, snapshot, now = _synthetic_approval_snapshot(tmp_path)
    assert has_explicit_eur_reserve_release(snapshot, now=now)
    # Reproduces the integrated V16 commitment: it exceeds the legacy $250
    # threshold but a real, validated card explicitly releases EUR 14.
    commitment = Decimal('251.3887771886882647485714158')
    assert legacy_usd_budget_exceeded(commitment, snapshot, now=now) is False


@pytest.mark.parametrize('case', ['missing', 'forged_caps', 'forged_source', 'wrong_scope', 'expired',
                                 'changed_source', 'current_caps'])
def test_reserve_release_rejects_missing_forged_stale_or_mismatched_authority(tmp_path, case):
    from datetime import timedelta
    import hashlib
    _book, _req, _plan, _card, path, snapshot, now = _synthetic_approval_snapshot(
        tmp_path, release_reserve=(case != 'current_caps'))
    if case == 'missing':
        snapshot = None
    elif case == 'forged_caps':
        snapshot['caps'] = {**snapshot['caps'], 'working_eur':'266', 'protected_eur':'34'}
    elif case == 'forged_source':
        path.write_text('{}')
        snapshot['authority']['sha256'] = hashlib.sha256(b'{}').hexdigest()
    elif case == 'wrong_scope':
        snapshot['scope_id'] = 'SYNTHETIC-OTHER-SCOPE'
    elif case == 'expired':
        now += timedelta(days=2)
    elif case == 'changed_source':
        path.write_text('{}')
    # current_caps is a valid, source-bound card but retains at least EUR 50 protected.
    commitment = Decimal('251.3887771886882647485714158')
    assert not has_explicit_eur_reserve_release(snapshot, now=now)
    assert legacy_usd_budget_exceeded(commitment, snapshot, now=now)


def test_budget_ledger_uses_source_card_and_eur_guard_for_over_250_commitment(tmp_path):
    _book, req, plan, _card, _path, _snapshot, _now = _synthetic_approval_snapshot(tmp_path)
    # Simulate synthetic already-approved funding inside this disposable ledger.
    state = _book.inspect()
    state['funding_remaining'] = {'primary':'251'}
    _book._write(state)
    decision = _book.reserve(req, plan=plan)
    assert decision.status == 'reserved', decision.reasons
    assert len(_book.inspect()['reservations']) == 1


def test_budget_ledger_keeps_legacy_rejection_without_source_bound_release(tmp_path):
    _book, req, plan, _card, _path, _snapshot, _now = _synthetic_approval_snapshot(
        tmp_path, release_reserve=False)
    state = _book.inspect()
    state['funding_remaining'] = {'primary':'251'}
    _book._write(state)
    decision = _book.reserve(req, plan=plan)
    assert decision.reasons == ('budget_exhausted',)
    assert not _book.inspect()['reservations']
