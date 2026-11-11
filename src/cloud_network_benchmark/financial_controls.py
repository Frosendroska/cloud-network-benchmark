"""Local financial admission only; no provider/network calls or approval generation.

The human-owned, hashed card is the trust boundary. This is deliberately full-cash
accounting: no credit or reimbursement offsets and no automatic headroom release.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FinancialError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def amount(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise FinancialError('financial_amount_invalid')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise FinancialError('financial_amount_invalid') from None
    if not result.is_finite() or result < 0:
        raise FinancialError('financial_amount_invalid')
    return result


def liability(value: Any) -> Decimal:
    try:
        return amount(value).quantize(Decimal('.01'), rounding=ROUND_CEILING)
    except InvalidOperation:
        raise FinancialError('financial_amount_invalid') from None


def ceiling(value: Any) -> Decimal:
    try:
        return amount(value).quantize(Decimal('.01'), rounding=ROUND_FLOOR)
    except InvalidOperation:
        raise FinancialError('financial_amount_invalid') from None


def provider_liability(components: Mapping[str, Any], rate: Decimal, fixed_fees: Decimal) -> Decimal:
    """Convert the USD amount actually reserved by F11, including its upward rounding."""
    dollars = sum((amount(c['amount_usd']) for c in components.values()), Decimal(0))
    dollars = dollars.quantize(Decimal('.0001'), rounding=ROUND_CEILING)
    return liability(dollars * rate + fixed_fees)


def stamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FinancialError('financial_timestamp_invalid')
    return value.astimezone(timezone.utc)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FinancialError('financial_duplicate_key')
        result[key] = value
    return result


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class Source(StrictModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    def read(self) -> bytes:
        path = Path(self.path)
        if (not path.is_absolute() or not path.is_file()
                or any(p.is_symlink() for p in (path, *path.parents))):
            raise FinancialError('financial_source_missing')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.sha256:
            raise FinancialError('financial_source_changed')
        return raw

    def json(self) -> dict:
        value = json.loads(self.read(), object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(FinancialError('financial_amount_invalid')))
        if not isinstance(value, dict):
            raise FinancialError('financial_source_invalid')
        return value


class CheckedSource(Source):
    official_urls: list[str] = Field(min_length=1)
    checked_at: datetime
    valid_until: datetime

    @field_validator('official_urls')
    @classmethod
    def urls(cls, value):
        if any(not u.startswith('https://') for u in value):
            raise ValueError('official source URLs require HTTPS')
        return value

    def check(self, now: datetime, end: datetime) -> None:
        self.read()
        age = stamp(now) - stamp(self.checked_at)
        if age < timedelta(0) or age > timedelta(hours=24) or stamp(self.valid_until) < stamp(end):
            raise FinancialError('financial_quote_stale')


class FxQuote(CheckedSource):
    currency: Literal['USD']
    budget_currency: Literal['EUR']
    conservative_eur_per_unit: str

    @field_validator('conservative_eur_per_unit')
    @classmethod
    def rate(cls, value):
        if amount(value) <= 0:
            raise ValueError('positive sourced conservative FX required')
        return value


class Caps(StrictModel):
    project_eur: str
    working_eur: str
    protected_eur: str
    personal_cash_eur: str
    run_eur: str
    batch_eur: str
    stage_eur: str
    providers_eur: dict[str, str]

    @field_validator('project_eur', 'working_eur', 'protected_eur', 'personal_cash_eur',
                     'run_eur', 'batch_eur', 'stage_eur')
    @classmethod
    def money(cls, value):
        ceiling(value)
        return value


class FinancialAuthorization(StrictModel):
    schema_version: Literal[1]
    kind: Literal['explicit-human-paid-approval']
    evidence_id: str = Field(min_length=1)
    approved_by: str = Field(min_length=1)
    approved_at: datetime
    valid_from: datetime
    valid_until: datetime
    currency: Literal['EUR']
    ledger_scope_id: str = Field(min_length=1)
    batch_id: str = Field(min_length=1)
    stage_id: str = Field(min_length=1)
    approved_run_count: int = Field(strict=True, gt=0)
    entries: list[dict[str, Any]] = Field(min_length=1)
    caps: Caps
    price: CheckedSource
    fx: FxQuote
    external_liabilities: CheckedSource
    # No defaults, credit/receipt offsets, or recommendation-derived permission.
    other_gross_eur: str
    other_personal_cash_eur: str
    fx_card_fee_rate: str
    fixed_fees_eur: str
    funding_treatment: Literal['full-personal-cash-no-offsets']


def require_transfer_capability() -> None:
    """No configuration/card can turn opaque SCP into a cumulative byte guard."""
    raise FinancialError('cumulative_transfer_guard_not_implemented')


def _serialize(value: Any) -> Any:
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, '__dataclass_fields__'):
        from dataclasses import asdict
        return asdict(value)
    raise TypeError(type(value).__name__)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=_serialize,
                                    separators=(',', ':')).encode()).hexdigest()


def financial_binding(plan: Any, request: Any) -> dict[str, Any]:
    """Exact approval entry, including both effective inputs and original source bytes."""
    campaign = plan.resolved_campaign
    result = {'campaign_id': request.campaign_id, 'config_id': request.config_id,
        'config_sha256': campaign.config_source.sha256, 'resolved_sha256': campaign.resolved_sha256,
        'mode': str(request.mode), 'priority': request.priority,
        'run_ids': dict(plan.child_identities), 'namespaces': dict(plan.namespaces),
        'lifetime_cap_seconds': request.lifetime_cap_seconds,
        'cost_sha256': plan.costs.payload_sha256, 'cost_envelopes_sha256': digest(plan.cost_envelopes),
        'allowance_sha256': digest(plan.allowance),
        'runtime': dict(plan.financial_binding),
        'resources': {str(o.provider): {'provider_config': o.provider_config.model_dump(mode='json'),
            'vms': 2, 'disks': 2, 'public_addresses': 2} for o in campaign.observations},
        'scheduled_start': campaign.scheduled_start.isoformat() if campaign.scheduled_start else None}
    return json.loads(json.dumps(result, default=_serialize))


def _read_authority(reference: Mapping[str, Any]) -> tuple[Source, FinancialAuthorization]:
    source = Source(path=reference['source_path'], sha256=reference['source_sha256'])
    return source, FinancialAuthorization.model_validate(source.json())


def validate_authority(reference: Mapping[str, Any], *, binding: Mapping[str, Any],
                       scope_id: str, now: datetime, end: datetime) -> tuple[Source, FinancialAuthorization]:
    source, card = _read_authority(reference)
    now, end = stamp(now), stamp(end)
    if not (stamp(card.approved_at) <= now and stamp(card.valid_from) <= now < stamp(card.valid_until)
            and now <= end <= stamp(card.valid_until)):
        raise FinancialError('financial_authorization_expired')
    if card.ledger_scope_id != scope_id:
        raise FinancialError('financial_scope_mismatch')
    entries = [digest(e) for e in card.entries]
    run_ids = [run for e in card.entries for run in e['run_ids'].values()]
    if (len(set(entries)) != len(entries) or len(set(run_ids)) != len(run_ids)
            or card.approved_run_count != len(run_ids)
            or digest(binding) not in entries):
        raise FinancialError('financial_scope_mismatch')
    runtime = binding.get('runtime', {})
    if (not runtime or not runtime.get('implementation_revision') or not runtime.get('sources')
            or runtime.get('initialized_revisions') != [runtime['implementation_revision']]
            or not runtime.get('runtime_source')):
        raise FinancialError('financial_runtime_binding_missing')
    for item in runtime['sources']:
        Source.model_validate(item).read()
    # The complete effective provider/runtime digest is part of the reviewed entry.
    if not runtime.get('effective_runtime_sha256'):
        raise FinancialError('financial_runtime_binding_missing')
    providers = set(binding['run_ids'])
    if set(card.caps.providers_eur) != {p for e in card.entries for p in e['run_ids']}:
        raise FinancialError('financial_scope_mismatch')
    if not providers <= set(card.caps.providers_eur):
        raise FinancialError('financial_scope_mismatch')
    caps = card.caps
    if (ceiling(caps.project_eur) > 300
            or ceiling(caps.working_eur) + liability(caps.protected_eur) > ceiling(caps.project_eur)):
        raise FinancialError('financial_ceiling_exceeded')
    for value in caps.providers_eur.values():
        ceiling(value)
    card.price.check(now, end)
    if card.price.sha256 != binding['cost_sha256']:
        raise FinancialError('financial_price_mismatch')
    prices = card.price.json()
    if (prices.get('currency') != 'USD' or prices.get('refresh_required_before_paid_execution', False)
            or prices.get('prices_checked_on') != stamp(card.price.checked_at).date().isoformat()):
        raise FinancialError('financial_price_mismatch')
    card.fx.check(now, end)
    fx = card.fx.json()
    # Canonical reviewed FX capture must agree with the card, not a free floating factor.
    if (any(fx.get(key) != card.fx.model_dump(mode='json')[key] for key in
           ('currency', 'budget_currency', 'conservative_eur_per_unit', 'official_urls'))
            or stamp(datetime.fromisoformat(fx['checked_at'])) != stamp(card.fx.checked_at)
            or stamp(datetime.fromisoformat(fx['valid_until'])) != stamp(card.fx.valid_until)):
        raise FinancialError('financial_fx_mismatch')
    card.external_liabilities.check(now, end)
    other = card.external_liabilities.json()
    if (other.get('ledger_scope_id') != scope_id or other.get('currency') != 'EUR'
            or amount(other.get('gross_eur')) != amount(card.other_gross_eur)
            or amount(other.get('personal_cash_eur')) != amount(card.other_personal_cash_eur)
            or other.get('excludes_this_ledger') is not True):
        raise FinancialError('financial_external_liabilities_mismatch')
    if amount(card.other_personal_cash_eur) != amount(card.other_gross_eur):
        raise FinancialError('financial_external_liabilities_mismatch')
    amount(card.fx_card_fee_rate); amount(card.fixed_fees_eur)
    return source, card


def validate_paid_financial_plan(plan: Any, request: Any, *, scope_id: str, now: datetime) -> dict | None:
    """Also supports explicit synthetic accounting exercises without provider dispatch."""
    enabled = plan.execution_scope == 'paid_request' or bool(plan.financial_binding)
    if not enabled:
        return None
    try:
        binding = financial_binding(plan, request)
        end = stamp(now) + timedelta(seconds=request.lifetime_cap_seconds)
        source, card = validate_authority(plan.prerequisites['paid_authorization'], binding=binding,
                                         scope_id=scope_id, now=now, end=end)
        rate = amount(card.fx.conservative_eur_per_unit) * (1 + amount(card.fx_card_fee_rate))
        if any(e['priced_on'] != stamp(card.price.checked_at).date().isoformat()
               for e in plan.cost_envelopes.values()):
            raise FinancialError('financial_price_mismatch')
        by_provider = {p: str(provider_liability(e['components'], rate, amount(card.fixed_fees_eur)))
                       for p, e in plan.cost_envelopes.items()}
        gross = sum((amount(v) for v in by_provider.values()), Decimal(0))
        if gross > ceiling(card.caps.run_eur):
            raise FinancialError('financial_run_ceiling_exceeded')
        if gross + liability(card.other_personal_cash_eur) > ceiling(card.caps.personal_cash_eur):
            raise FinancialError('financial_cash_ceiling_exceeded')
        if (gross + liability(card.other_gross_eur) > ceiling(card.caps.working_eur)
                or gross + liability(card.other_gross_eur) + liability(card.caps.protected_eur)
                > ceiling(card.caps.project_eur)):
            raise FinancialError('financial_project_ceiling_exceeded')
        if gross > ceiling(card.caps.batch_eur) or gross > ceiling(card.caps.stage_eur):
            raise FinancialError('financial_scope_ceiling_exceeded')
        if any(amount(v) > ceiling(card.caps.providers_eur[p]) for p, v in by_provider.items()):
            raise FinancialError('financial_provider_ceiling_exceeded')
        snapshot = {'authority': source.model_dump(), 'binding': binding, 'batch_id': card.batch_id,
            'stage_id': card.stage_id, 'caps': card.caps.model_dump(), 'rate': str(rate),
            'gross_eur': str(gross), 'cash_eur': str(gross), 'providers_eur': by_provider,
            'external_gross_eur': str(liability(card.other_gross_eur)),
            'external_cash_eur': str(liability(card.other_personal_cash_eur)), 'scope_id': scope_id}
        if plan.execution_scope == 'paid_request':
            require_transfer_capability()
        return snapshot
    except FinancialError:
        raise
    except (ValueError, TypeError, KeyError, OSError, AttributeError) as exc:
        raise FinancialError('financial_authorization_invalid') from exc


def check_shared_finances(state: dict, snapshot: dict, *, remaining_usd: Decimal) -> None:
    """Called under the SAME exclusive ledger lock as the USD reservation."""
    records = list(state['reservations'].values())
    if any(not isinstance(r.get('financial'), dict) for r in records):
        raise FinancialError('financial_legacy_liability_unconverted')
    for record in records:
        validate_stored_commitment(record, scope_id=state['scope_id'])
    prior = [r['financial'] for r in records]
    caps = Caps.model_validate(snapshot['caps'])
    # Never release original maxima or allow later FX changes to discount old exposure.
    rate = max([amount(snapshot['rate'])] + [amount(f['rate']) for f in prior])
    gross = [max(amount(f['gross_eur']),
        liability(max(amount(r['reserved_cost_usd']), amount(r.get('incurred_usd', 0))) * rate)
        + max(amount(f['gross_eur']) - liability(amount(r['reserved_cost_usd']) * amount(f['rate'])), Decimal(0)))
             for r, f in zip(records, prior)]
    external = max([amount(snapshot['external_gross_eur'])] + [amount(f['external_gross_eur']) for f in prior])
    cash_external = max([amount(snapshot['external_cash_eur'])] + [amount(f['external_cash_eur']) for f in prior])
    future = liability(remaining_usd * rate)
    total = external + sum(gross) + amount(snapshot['gross_eur']) + future
    cash = cash_external + sum(gross) + amount(snapshot['cash_eur']) + future
    # Keep high-water external liabilities across subsequent approval/source changes.
    snapshot['external_gross_eur'], snapshot['external_cash_eur'] = str(external), str(cash_external)
    if total > ceiling(caps.working_eur) or total + liability(caps.protected_eur) > ceiling(caps.project_eur):
        raise FinancialError('financial_project_ceiling_exceeded')
    if cash > ceiling(caps.personal_cash_eur):
        raise FinancialError('financial_cash_ceiling_exceeded')
    for label, cap in [('batch_id', caps.batch_eur), ('stage_id', caps.stage_eur)]:
        used = sum((g for f, g in zip(prior, gross) if f[label] == snapshot[label]), Decimal(0))
        if used + amount(snapshot['gross_eur']) > ceiling(cap):
            raise FinancialError('financial_' + label + '_ceiling_exceeded')
    for provider, value in snapshot['providers_eur'].items():
        used = Decimal(0)
        for r, f in zip(records, prior):
            if provider not in f['providers_eur']:
                continue
            reserved = sum((amount(c['amount_usd']) for c in
                r['plan']['cost_envelopes'][provider]['components'].values()), Decimal(0))
            reserved = reserved.quantize(Decimal('.0001'), rounding=ROUND_CEILING)
            billing = r.get('provider_billing', {}).get(provider, {})
            dollars = max(reserved, amount(billing.get('actual_usd') or 0), amount(billing.get('estimate_usd') or 0))
            # Preserve the original fixed-fee/rounding margin while revaluing all
            # unsettled USD exposure at the highest reviewed conservative rate.
            margin = max(amount(f['providers_eur'][provider]) - liability(reserved * amount(f['rate'])), Decimal(0))
            used += max(amount(f['providers_eur'][provider]), liability(dollars * rate) + margin)
        if used + amount(value) > ceiling(caps.providers_eur[provider]):
            raise FinancialError('financial_provider_ceiling_exceeded')


def validate_stored_commitment(record: dict, *, scope_id: str) -> None:
    """Structural durable-state checks only; cleanup never needs fresh source files."""
    f = record['financial']
    Source.model_validate(f['authority'])
    Caps.model_validate(f['caps'])
    if (f['scope_id'] != scope_id or set(f['providers_eur']) != set(record['plan']['providers'])
            or amount(f['rate']) <= 0 or amount(f['cash_eur']) != amount(f['gross_eur'])
            or sum((amount(v) for v in f['providers_eur'].values()), Decimal(0)) != amount(f['gross_eur'])
            or amount(f['gross_eur']) < liability(amount(record['reserved_cost_usd']) * amount(f['rate']))
            or amount(f['external_cash_eur']) != amount(f['external_gross_eur'])):
        raise FinancialError('financial_ledger_invalid')


def revalidate_financial_record(record: dict, *, state: dict, now: datetime, end: datetime) -> None:
    if record.get('execution_scope') == 'offline_simulation':
        return
    try:
        financial = record['financial']
        source = financial['authority']
        validate_authority({'source_path': source['path'], 'source_sha256': source['sha256']},
            binding=financial['binding'], scope_id=financial['scope_id'], now=now, end=end)
        if state.get('cleanup_blocked') or state.get('budget_blocked'):
            raise FinancialError('financial_outstanding_liability')
        # Existing records already include this reservation: add no new exposure.
        check_shared_finances(state, {**financial, 'gross_eur':'0', 'cash_eur':'0',
            'providers_eur':dict.fromkeys(financial['providers_eur'], '0')},
            remaining_usd=sum((amount(v) for v in state['funding_remaining'].values()), Decimal(0)))
        require_transfer_capability()
    except FinancialError:
        raise
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise FinancialError('financial_authorization_invalid') from exc
