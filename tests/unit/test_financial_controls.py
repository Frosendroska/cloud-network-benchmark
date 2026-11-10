"""Synthetic, non-authorizing financial policy regressions."""
from decimal import Decimal
import pytest
from cloud_network_benchmark.financial_controls import liability, ceiling, FinancialAuthorization

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
