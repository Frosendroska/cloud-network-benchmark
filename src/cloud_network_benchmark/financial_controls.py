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

    def check(self, now: datetime, end: datetime, *, freshness_at: datetime | None = None) -> None:
        self.read()
        age = stamp(freshness_at if freshness_at is not None else now) - stamp(self.checked_at)
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
    # Optional so historical EUR 300 cards and stored snapshots keep their
    # original shape and meaning. Elevated ceilings require this exact source.
    financial_policy_amendment: Source | None = None
    # A separate human source permits a later fixed-family continuation card;
    # it never changes the semantics of the consumed O2 card.
    continuation_authorization: Source | None = None
    # Optional exact human amendment removes only the later EUR48 allocation epoch.
    continuation_exposure_amendment: Source | None = None
    # No defaults, credit/receipt offsets, or recommendation-derived permission.
    other_gross_eur: str
    other_personal_cash_eur: str
    fx_card_fee_rate: str
    fixed_fees_eur: str
    funding_treatment: Literal['full-personal-cash-no-offsets']


_O2_AMENDMENT_SHA256 = '6c2253bfc2e92714289a6932d1e9061f785931b5328e922027122965f58cf0e1'
_O3_CONTINUATION_SHA256 = '3d30cf5eeb0b59714ffca6fd91cb315e87e9ae68caa232a11282e71d3d9ed49e'
_O4_RETRY_CAP_REMOVAL_SHA256 = 'd42f4e3c76454e5946f73483d57d37c22a88d90e03595701b03c806c4cadf576'
_O3_LEGACY_USD_GUARD_CORRECTION_SHA256 = '1d273f0a82aafa5f9aa92a5dd8a021d4a5582fb3963a2b76acadff4e8d9e9228'
_O3_LEGACY_USD_GUARD_CARD_SHA256 = '7eee878de229e750c1ef239882be72cb6696794bd9dd868580e7590c49b113f2'
_O3_LEGACY_USD_GUARD_LIMITS = {
    'child_run_eur': '8', 'new_reserve_release_eur': '0',
    'next_fresh_stage_eur': '16', 'no_cal_day_week_pilots_or_deliberate_failure_tests': True,
    'one_active_batch_at_a_time': True, 'total_new_exposure_eur': '48',
    'whole_thesis_and_cash_eur': '400',
}
_O3_EPOCH_ANCHORS = {
    ('7a67fa5ece65803d1034c69c67dd4f0b6b2813a1801fbec71511e3e85bf697f4',
     'acceptance-20261126T034200Z-c9f172c0'),
    ('f013ad7f437b7dde16bd3b9067f3e1307d0e4653ae602f2496600d49ddaa418f',
     'acceptance-20261126T121039Z-62e1771c'),
}
_O2_SUPERSEDED_AMENDMENT_SHA256 = '5749cf6af58af9182e2a0df56b6cfe222b8b34d3cd9b099afab95c34e350d807'
_O2_EVIDENCE_ID = 'user-cross-chat-message-01a21754-0e00-72c3-b96e-878d1119e8a8'
_O2_FIRST_OBSERVED = '2026-11-26T10:08:34Z'
_O2_PENDING_CARD_SHA256 = 'a61b9bc0fdedb526459218aab3ada6c0d5974611aec46a84847c1cb6120fc6a4'
_O2_POLICY_END = datetime(2026, 11, 27, 7, 0, tzinfo=timezone.utc)
_O2_LATEST_DISPATCH = datetime(2026, 11, 27, 5, 45, tzinfo=timezone.utc)
_O2_FIXED_FAMILY_BATCH_ID = 'fixed-pre-day-v1'
_O2_LIMITS = {
    'whole_thesis_ceiling_eur': '400', 'cumulative_personal_cash_ceiling_eur': '400',
    'new_batch_ceiling_eur': '16', 'fixed_family_combined_ceiling_eur': '233',
    'overall_new_exposure_ceiling_eur': '48',
    'remaining_additional_reserve_release_ceiling_eur': '13',
    'maximum_cumulative_reserve_release_eur': '50',
    'protected_after_maximum_release_eur': '0', 'additional_attempt_count': 1,
    'policy_valid_until_utc': '2026-11-27T07:00:00Z',
    'latest_dispatch_utc': '2026-11-27T05:45:00Z',
    'chair_allowance_eur': '300', 'additional_personal_exposure_eur': '100',
    'reimbursement_assumed': False,
}
_O2_SCOPE = {
    'cases': ['S1', 'S3'], 'clouds': ['aws', 'azure', 'gcp'],
    'later_exact_source_bound_card_required': True,
    'new_guard_and_complete_operator_qualification_required': True,
    'no_day_week': True, 'no_deliberate_failure_tests': True,
    'no_paid_cal': True, 'no_pilots': True,
    'original_caps_receipts_cleanup_native_and_gcs_preserved': True,
    'simultaneous': True,
}

_O3_CONTINUATION_LIMITS = {
    'cumulative_personal_cash_eur': '400', 'current_o2_family_eur': '233',
    'current_o2_fresh_eur': '16', 'current_o2_latest_dispatch_utc': '2026-11-27T05:45:00Z',
    'current_o2_policy_valid_until_utc': '2026-11-27T07:00:00Z',
    'current_o2_working_eur': '302', 'current_per_provider_child_run_eur': '8',
    'new_reserve_authorized_by_this_message_eur': '0', 'overall_new_exposure_eur': '48',
    'remaining_previously_authorized_reserve_eur': '13', 'resource_lifetime_seconds': 4200,
    'whole_thesis_eur': '400',
}
_O3_CONTINUATION_SCOPE = {
    'cases': ['S1', 'S3'], 'clouds': ['aws', 'azure', 'gcp'],
    'continue_until_verified': True, 'no_day_week': True,
    'no_deliberate_failure_tests': True, 'no_paid_cal': True, 'no_pilots': True,
    'one_attempt_per_exact_card': True,
    'prior_attempt_terminal_cleanup_and_liability_reconciled_before_successor': True,
    'root_may_accept_new_exact_cards': True, 'same_fixed_scope_only': True,
    'simultaneous': True,
}
_O3_CONFIG_PRIORITIES = {'exp-903-acceptance': 'P0', 'exp-003-acceptance': 'P1'}


def _validate_o3_continuation(card: FinancialAuthorization, *, now: datetime,
                              end: datetime, existing_authorization: bool = False) -> Source:
    ref = card.continuation_authorization
    if ref is None:
        raise FinancialError('financial_amendment_invalid')
    ref = validate_o3_continuation_source(ref, now=now, end=end,
        existing_authorization=existing_authorization)
    if card.continuation_exposure_amendment is not None:
        validate_o4_retry_cap_removal_source(card.continuation_exposure_amendment)
    if stamp(card.valid_until) > _O2_POLICY_END:
        raise FinancialError('financial_authorization_expired')
    return ref


def validate_o3_continuation_source(ref: Source, *, now: datetime, end: datetime,
                                    existing_authorization: bool = False) -> Source:
    """Validate the exact S1/S3 continuation source during bounded cost planning."""
    if not isinstance(ref, Source) or ref.sha256 != _O3_CONTINUATION_SHA256:
        raise FinancialError('financial_amendment_invalid')
    payload = ref.json()
    if (payload.get('schema_version') != 1
            or payload.get('kind') != 'genuine-human-fixed-s1-s3-continuation'
            or payload.get('evidence_id') != 'direct-current-execution-chat-user-s1-s3-until-verified'
            or payload.get('ceiling_amendment_sha256') != _O2_AMENDMENT_SHA256
            or payload.get('dispatch_performed') is not False
            or payload.get('funding_applied') is not False
            or payload.get('ledger_mutated') is not False
            or payload.get('reserve_applied') is not False
            or payload.get('not_a_claim_of_any_cloud_success') is not True
            or payload.get('preserved_limits') != _O3_CONTINUATION_LIMITS
            or payload.get('scope') != _O3_CONTINUATION_SCOPE):
        raise FinancialError('financial_amendment_invalid')
    now, end = stamp(now), stamp(end)
    if (now > _O2_POLICY_END or end > _O2_POLICY_END
            or (not existing_authorization and now > _O2_LATEST_DISPATCH)):
        raise FinancialError('financial_authorization_expired')
    return ref


def validate_o4_retry_cap_removal_source(ref: Source) -> Source:
    """Validate the exact source removing the separate EUR48 successor allocation cap."""
    if not isinstance(ref, Source) or ref.sha256 != _O4_RETRY_CAP_REMOVAL_SHA256:
        raise FinancialError('financial_amendment_invalid')
    payload = ref.json()
    expected_limits = {
        'chair_reimbursement_allowance_eur': '300',
        'cumulative_personal_cash_eur': '400',
        'latest_dispatch_utc': '2026-11-27T05:45:00Z',
        'maximum_personal_exposure_above_chair_eur': '100',
        'new_reserve_authorized_by_this_message_eur': '0',
        'per_batch_eur': '16', 'per_provider_child_run_eur': '8',
        'plan_review_timeout_seconds': 120,
        'policy_valid_until_utc': '2026-11-27T07:00:00Z',
        'resource_lifetime_seconds': 4200, 'whole_thesis_eur': '400',
    }
    expected_change = {
        'new_reserve_release_eur': '0', 'per_batch_eur': '16',
        'removed_separate_total_new_exposure_eur': '48',
        'replacement_separate_total_new_exposure_cap': None,
        'root_card_acceptance_remains_delegated': True,
        'whole_thesis_and_cumulative_cash_ceiling_eur': '400',
    }
    expected_scope = {**_O3_CONTINUATION_SCOPE,
        'all_retained_commitments_and_unknown_bills_preserved': True,
        'one_active_batch_at_a_time': True}
    if (payload.get('schema_version') != 1
            or payload.get('kind') != 'genuine-human-fixed-s1-s3-retry-cap-removal'
            or payload.get('evidence_id') != 'direct-current-execution-chat-user-remove-eur48-retry-cap-20261126'
            or payload.get('raw_user_message_sha256') != 'c7f2618075b186d1da38fe1917eaba979637db85abb7449a1128ad9a7d55a4ff'
            or payload.get('prior_continuation_sha256') != _O3_CONTINUATION_SHA256
            or payload.get('ceiling_amendment_sha256') != _O2_AMENDMENT_SHA256
            or payload.get('legacy_guard_correction_sha256') != _O3_LEGACY_USD_GUARD_CORRECTION_SHA256
            or payload.get('authority_change') != expected_change
            or payload.get('preserved_limits') != expected_limits
            or payload.get('scope') != expected_scope
            or payload.get('dispatch_performed') is not False
            or payload.get('funding_applied') is not False
            or payload.get('ledger_mutated') is not False
            or payload.get('reserve_applied') is not False
            or payload.get('not_a_claim_of_any_cloud_success') is not True
            or payload.get('source') != 'Direct human user message in this execution chat; exact literal text retained. No stable message UUID is asserted.'):
        raise FinancialError('financial_amendment_invalid')
    return ref


def _validate_o3_epoch_allocation(total_including_candidate: Decimal,
                                  candidate_allocation: Decimal,
                                  exposure_amendment: Source | None) -> None:
    """Keep the EUR48 legacy epoch unless the exact O4 source removes that limit."""
    if candidate_allocation != Decimal('16'):
        raise FinancialError('financial_continuation_exposure_exceeded')
    if exposure_amendment is None:
        if total_including_candidate > Decimal('48'):
            raise FinancialError('financial_continuation_exposure_exceeded')
        return
    validate_o4_retry_cap_removal_source(exposure_amendment)


def validate_o3_legacy_usd_guard_source(ref: Source) -> Source:
    """Validate the approved code-level guard correction; it grants no new reserve."""
    if not isinstance(ref, Source) or ref.sha256 != _O3_LEGACY_USD_GUARD_CORRECTION_SHA256:
        raise FinancialError('financial_amendment_invalid')
    payload = ref.json()
    expected_message = {
        'raw_message': ('Approve the source-bound legacy USD guard correction for the already-approved '
            'EUR400 S1+S3 continuation, with no new reserve release or scope increase. Please continue '
            'until the real run is fully verified. Dont ask me for a card approval anymore. I authorize '
            'you to run the loop and the experiments until you get the S1/S3 done and verified. Dont ask me'),
        'raw_message_sha256': 'ad11fb0754ba0daa5b713938428972d6a63f19f0e43132b14858a96016deab7b',
        'thread_id': '01a20a74-68db-7c63-8c72-0fae7131a531',
        'verification': 'Direct human user message in this execution chat; no inferred message UUID',
    }
    if (payload.get('schema_version') != 1
            or payload.get('kind') != 'genuine-human-legacy-usd-guard-correction'
            or payload.get('evidence_id') != 'direct-current-execution-chat-user-legacy-usd-guard-approval'
            or payload.get('approved_by') != 'Katya'
            or payload.get('correction_approved') is not True
            or payload.get('ceiling_amendment_sha256') != _O2_AMENDMENT_SHA256
            or payload.get('continuation_source_sha256') != _O3_CONTINUATION_SHA256
            or payload.get('dispatch_performed') is not False
            or payload.get('funding_applied') is not False
            or payload.get('ledger_mutated') is not False
            or payload.get('no_routine_card_approval_required_within_existing_authority') is not True
            or payload.get('limits_unchanged') != _O3_LEGACY_USD_GUARD_LIMITS
            or payload.get('reviewed_correction_card') != {
                'path': 'tracking/approvals/PRE_DAY_LEGACY_USD_GUARD_FIX_APPROVAL.md',
                'sha256': _O3_LEGACY_USD_GUARD_CARD_SHA256}
            or payload.get('source_message') != expected_message):
        raise FinancialError('financial_amendment_invalid')
    return ref


def _validate_o2_amendment(card: FinancialAuthorization, *, now: datetime,
                           end: datetime, existing_authorization: bool = False) -> Source:
    ref = card.financial_policy_amendment
    if ref is None:
        raise FinancialError('financial_ceiling_exceeded')
    if ref.sha256 != _O2_AMENDMENT_SHA256:
        raise FinancialError('financial_amendment_invalid')
    payload = ref.json()
    if (payload.get('schema_version') != 1
            or payload.get('kind') != 'genuine-human-o2-budget-amendment'
            or payload.get('evidence_id') != _O2_EVIDENCE_ID
            or payload.get('approved_by') != 'Katya'
            or payload.get('approved_pending_card') != {
                'path': 'tracking/approvals/PRE_DAY_APPROVAL_CARD_O2_FINANCIAL_UNBLOCK.md',
                'sha256': _O2_PENDING_CARD_SHA256}
            or payload.get('limits') != _O2_LIMITS
            or payload.get('scope') != _O2_SCOPE
            or payload.get('supersedes_policy_source') != {
                'path': '/u/halle/brek/home_at/cnb/private/pre-day-release-20261122/fixed-batch-KoPvIO/protected-compact/overnight-acceptance-O2-20261126/genuine-human-budget-amendment-O2-01.json',
                'sha256': _O2_SUPERSEDED_AMENDMENT_SHA256}
            or payload.get('change') != {
                'increase_from_interim_302_eur': '98',
                'increase_from_original_chair_300_eur': '100',
                'new_attempts_authorized_by_this_update': 0,
                'new_reserve_release_authorized_eur': '0',
                'new_whole_thesis_and_cash_ceiling_eur': '400',
                'not_an_instruction_to_launch': True,
                'prior_whole_thesis_and_cash_ceiling_eur': '302'}
            or payload.get('source_message') != {
                'raw_message_sha256': 'b111f2c0c080aeda30a021833f4802916ddd20c4a2245e5c86562ded06050813',
                'thread_id': '01a20d07-cdde-7dd0-bbc2-314c1c37dc1f',
                'turn_id': '01a21754-0e00-72c3-b96e-878d1119e8a8',
                'verification': 'Direct human userMessage read with Codex read_thread, not another agent assertion'}
            or payload.get('dispatch_authorized_now') is not False
            or payload.get('financial_authorization_materialized') is not False
            or payload.get('funding_applied') is not False
            or payload.get('ledger_mutated') is not False
            or payload.get('reserve_applied') is not False
            or payload.get('user_reported_billing', {}).get('status')
                != 'user_observation_not_final_invoice_or_VM_absence_proof'):
        raise FinancialError('financial_amendment_invalid')
    first_observed = stamp(datetime.fromisoformat(payload['first_observed_at_utc'].replace('Z', '+00:00')))
    now, end = stamp(now), stamp(end)
    if (first_observed != stamp(datetime.fromisoformat(_O2_FIRST_OBSERVED.replace('Z', '+00:00')))
            or first_observed > now
            or (not existing_authorization and now > _O2_LATEST_DISPATCH)
            or now > _O2_POLICY_END or end > _O2_POLICY_END
            or stamp(card.valid_until) > _O2_POLICY_END):
        raise FinancialError('financial_authorization_expired')
    continuation = None
    if card.continuation_authorization is not None:
        continuation = _validate_o3_continuation(card, now=now, end=end,
                                                existing_authorization=existing_authorization)
    elif card.continuation_exposure_amendment is not None:
        raise FinancialError('financial_amendment_invalid')
    if (card.approved_at < first_observed
            or ceiling(card.caps.project_eur) > Decimal('400')
            or ceiling(card.caps.personal_cash_eur) > Decimal('400')
            or ceiling(card.caps.working_eur) > (Decimal('400') if continuation else Decimal('302'))
            or ceiling(card.caps.run_eur) > Decimal('8')
            or ceiling(card.caps.batch_eur) > (Decimal('400') if continuation else Decimal('233'))
            or ceiling(card.caps.stage_eur) > Decimal('16')
            or (continuation is not None and amount(card.caps.protected_eur) != Decimal('0'))):
        raise FinancialError('financial_ceiling_exceeded')
    entries = card.entries
    starts = {row.get('scheduled_start') for row in entries}
    if (card.batch_id != _O2_FIXED_FAMILY_BATCH_ID
            or len(entries) != 2 or card.approved_run_count != 6
            or {row.get('priority') for row in entries} != {'P0', 'P1'}
            or {row.get('mode') for row in entries} != {'acceptance'}
            or len(starts) != 1
            or len({row.get('campaign_id') for row in entries}) != 2
            or any(row.get('lifetime_cap_seconds') != 4200
                   or set(row.get('run_ids', {})) != {'aws', 'azure', 'gcp'}
                   or len(set(row.get('run_ids', {}).values())) != 3
                   for row in entries)
            or len({run for row in entries for run in row['run_ids'].values()}) != 6):
        raise FinancialError('financial_scope_mismatch')
    if (continuation is not None
            and {row.get('config_id'): row.get('priority') for row in entries} != _O3_CONFIG_PRIORITIES):
        raise FinancialError('financial_scope_mismatch')
    return ref


def _validate_o2_batch_identity(prior_financials: list[Mapping[str, Any]],
                                snapshot: Mapping[str, Any]) -> None:
    amendment = snapshot.get('financial_policy_amendment')
    if amendment is None:
        return
    current_authority = snapshot.get('authority', {}).get('sha256')
    continuation = snapshot.get('continuation_authorization')
    continuation_sha = continuation.get('sha256') if isinstance(continuation, Mapping) else None
    for prior in prior_financials:
        previous_amendment = prior.get('financial_policy_amendment')
        if (isinstance(previous_amendment, Mapping)
                and previous_amendment.get('sha256') == amendment.get('sha256')
                and (prior.get('authority', {}).get('sha256') != current_authority
                     or prior.get('batch_id') != snapshot.get('batch_id')
                     or prior.get('stage_id') != snapshot.get('stage_id'))):
            prior_stage = prior.get('stage_id')
            new_exact_card = (
                continuation_sha == _O3_CONTINUATION_SHA256
                and snapshot.get('batch_id') == _O2_FIXED_FAMILY_BATCH_ID
                and prior.get('batch_id') == _O2_FIXED_FAMILY_BATCH_ID
                and isinstance(current_authority, str)
                and current_authority != prior.get('authority', {}).get('sha256')
                and isinstance(prior_stage, str) and prior_stage != snapshot.get('stage_id'))
            if not new_exact_card:
                raise FinancialError('financial_amendment_batch_consumed')


def transfer_terms(envelope: Mapping[str, Any]) -> tuple[int, int]:
    """Read the source-bound payload cap and separately priced protocol allowance.

    The latter is a conservative *price input*, not a measured bound on SSH or
    provider-billed wire bytes. Both values belong to one two-VM provider run.
    """
    row = envelope.get('result_transfer')
    if not isinstance(row, Mapping) or set(row) != {
            'payload_limit_bytes', 'protocol_overhead_allowance_bytes',
            'protocol_overhead_priced_usd'}:
        raise FinancialError('financial_transfer_allowance_missing')
    payload = row['payload_limit_bytes']
    overhead = row['protocol_overhead_allowance_bytes']
    if (type(payload) is not int or payload <= 0 or type(overhead) is not int or overhead <= 0):
        raise FinancialError('financial_transfer_allowance_invalid')
    component = envelope.get('components', {}).get('traffic_export', {})
    try:
        quantity = amount(component['quantity'])
        price = amount(component['amount_usd'])
        overhead_price = amount(row['protocol_overhead_priced_usd'])
    except (KeyError, TypeError):
        raise FinancialError('financial_transfer_price_invalid') from None
    # The complete priced egress quantity must include the payload AND the
    # protocol/control allowance, even where measured traffic shares this row.
    if (component.get('unit') != 'GB-egress' or quantity * Decimal(1_000_000_000) < payload + overhead
            or price <= 0 or overhead_price <= 0 or overhead_price > price):
        raise FinancialError('financial_transfer_price_invalid')
    return payload, overhead


def require_transfer_capability(envelopes: Mapping[str, Mapping[str, Any]] | None = None) -> None:
    """Fail closed unless the production runner and paid source have a bounded path."""
    from .contracts.execution import SubprocessCommandRunner
    if not callable(getattr(SubprocessCommandRunner, 'run_bounded_copy', None)):
        raise FinancialError('financial_transfer_transport_unavailable')
    if envelopes is not None:
        if not envelopes:
            raise FinancialError('financial_transfer_allowance_missing')
        for envelope in envelopes.values():
            transfer_terms(envelope)


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
                       scope_id: str, now: datetime, end: datetime,
                       quote_freshness_at: datetime | None = None,
                       existing_authorization: bool = False) -> tuple[Source, FinancialAuthorization]:
    source, card = _read_authority(reference)
    now, end = stamp(now), stamp(end)
    if quote_freshness_at is not None and not (stamp(card.valid_from) <= stamp(quote_freshness_at) <= now):
        raise FinancialError('financial_quote_stale')
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
    if card.financial_policy_amendment is not None:
        _validate_o2_amendment(card, now=now, end=end,
                               existing_authorization=existing_authorization)
    elif card.continuation_authorization is not None:
        raise FinancialError('financial_amendment_invalid')
    elif ceiling(caps.project_eur) > 300:
        raise FinancialError('financial_ceiling_exceeded')
    if ceiling(caps.working_eur) + liability(caps.protected_eur) > ceiling(caps.project_eur):
        raise FinancialError('financial_ceiling_exceeded')
    for value in caps.providers_eur.values():
        ceiling(value)
    card.price.check(now, end, freshness_at=quote_freshness_at)
    if card.price.sha256 != binding['cost_sha256']:
        raise FinancialError('financial_price_mismatch')
    prices = card.price.json()
    if (prices.get('currency') != 'USD' or prices.get('refresh_required_before_paid_execution', False)
            or prices.get('prices_checked_on') != stamp(card.price.checked_at).date().isoformat()):
        raise FinancialError('financial_price_mismatch')
    card.fx.check(now, end, freshness_at=quote_freshness_at)
    fx = card.fx.json()
    # Canonical reviewed FX capture must agree with the card, not a free floating factor.
    if (any(fx.get(key) != card.fx.model_dump(mode='json')[key] for key in
           ('currency', 'budget_currency', 'conservative_eur_per_unit', 'official_urls'))
            or stamp(datetime.fromisoformat(fx['checked_at'])) != stamp(card.fx.checked_at)
            or stamp(datetime.fromisoformat(fx['valid_until'])) != stamp(card.fx.valid_until)):
        raise FinancialError('financial_fx_mismatch')
    card.external_liabilities.check(now, end, freshness_at=quote_freshness_at)
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
        if card.financial_policy_amendment is not None:
            snapshot['financial_policy_amendment'] = card.financial_policy_amendment.model_dump()
        if card.continuation_authorization is not None:
            snapshot['continuation_authorization'] = card.continuation_authorization.model_dump()
        if card.continuation_exposure_amendment is not None:
            snapshot['continuation_exposure_amendment'] = card.continuation_exposure_amendment.model_dump()
        if plan.execution_scope == 'paid_request':
            require_transfer_capability(plan.cost_envelopes)
        return snapshot
    except FinancialError:
        raise
    except (ValueError, TypeError, KeyError, OSError, AttributeError) as exc:
        raise FinancialError('financial_authorization_invalid') from exc


def has_explicit_eur_reserve_release(snapshot: Mapping[str, Any] | None, *, now: datetime) -> bool:
    """Whether the source-bound, currently valid card changes the reserve split."""
    if not isinstance(snapshot, Mapping):
        return False
    try:
        # A snapshot's caps are not authority by themselves. Re-read the human-
        # owned card and require exact scope and typed-cap agreement.
        source = Source.model_validate(snapshot['authority'])
        card = FinancialAuthorization.model_validate(source.json())
        caps = Caps.model_validate(snapshot['caps'])
        if card.continuation_authorization is not None:
            _validate_o2_amendment(card, now=now, end=now, existing_authorization=True)
            return False
        if card.financial_policy_amendment is not None:
            _validate_o2_amendment(card, now=now, end=now)
        elif ceiling(caps.project_eur) > Decimal('300'):
            return False
        return (snapshot.get('scope_id') == card.ledger_scope_id
                and caps == card.caps
                and stamp(card.approved_at) <= stamp(now)
                and stamp(card.valid_from) <= stamp(now) < stamp(card.valid_until)
                and ceiling(caps.project_eur) <= Decimal('400')
                and ceiling(caps.working_eur) + liability(caps.protected_eur)
                <= ceiling(caps.project_eur)
                and ceiling(caps.working_eur) > Decimal('250')
                and liability(caps.protected_eur) < Decimal('50'))
    except (FinancialError, ValueError, TypeError, KeyError):
        return False


def _has_source_bound_o3_continuation(snapshot: Mapping[str, Any] | None, *, now: datetime) -> bool:
    """Permit O3 to use EUR ledger caps without misrepresenting a new reserve release."""
    if not isinstance(snapshot, Mapping):
        return False
    try:
        source = Source.model_validate(snapshot['authority'])
        card = FinancialAuthorization.model_validate(source.json())
        continuation = Source.model_validate(snapshot['continuation_authorization'])
        amendment = Source.model_validate(snapshot['financial_policy_amendment'])
        exposure_amendment = (Source.model_validate(snapshot['continuation_exposure_amendment'])
            if snapshot.get('continuation_exposure_amendment') is not None else None)
        caps = Caps.model_validate(snapshot['caps'])
        if (card.continuation_authorization != continuation
                or card.financial_policy_amendment != amendment
                or continuation.sha256 != _O3_CONTINUATION_SHA256
                or amendment.sha256 != _O2_AMENDMENT_SHA256
                or exposure_amendment != card.continuation_exposure_amendment
                or caps != card.caps
                or snapshot.get('batch_id') != card.batch_id
                or snapshot.get('stage_id') != card.stage_id
                or snapshot.get('scope_id') != card.ledger_scope_id):
            return False
        _validate_o2_amendment(card, now=now, end=now, existing_authorization=True)
        return True
    except (FinancialError, ValueError, TypeError, KeyError):
        return False


def has_o3_continuation_cost_source(snapshot: Mapping[str, Any] | None, *, now: datetime) -> bool:
    """Validate source-bound, non-authorizing planning refs without granting paid admission."""
    if not isinstance(snapshot, Mapping):
        return False
    try:
        continuation = Source.model_validate(snapshot['continuation_authorization'])
        correction = Source.model_validate(snapshot['legacy_usd_guard_correction'])
        exposure_amendment = (Source.model_validate(snapshot['continuation_exposure_amendment'])
            if snapshot.get('continuation_exposure_amendment') is not None else None)
        scope_id = snapshot.get('scope_id')
        if not isinstance(scope_id, str) or not scope_id.strip():
            return False
        validate_o3_continuation_source(continuation, now=now, end=now)
        validate_o3_legacy_usd_guard_source(correction)
        if exposure_amendment is not None:
            validate_o4_retry_cap_removal_source(exposure_amendment)
        return True
    except (FinancialError, ValueError, TypeError, KeyError):
        return False


def legacy_usd_budget_exceeded(commitment: Decimal, snapshot: Mapping[str, Any] | None,
                               *, now: datetime) -> bool:
    """Apply the historical USD split unless the current source card replaces it."""
    if has_explicit_eur_reserve_release(snapshot, now=now):
        return False
    if _has_source_bound_o3_continuation(snapshot, now=now):
        return False
    return commitment > Decimal('250') or commitment + Decimal('50') > Decimal('300')


def _o3_epoch_group_keys(groups: Mapping[tuple[str, str], list[tuple[str, Mapping[str, Any]]]],
                         candidate_key: tuple[str, str]) -> set[tuple[str, str]]:
    """Select O1/O2 anchors and O3-authorized stages without reclassifying old family cards."""
    anchors = {key for key in groups if key in _O3_EPOCH_ANCHORS}
    for key, rows in groups.items():
        if key == candidate_key or key in _O3_EPOCH_ANCHORS:
            continue
        first_financial = rows[0][1].get('financial', {}) if rows else {}
        continuation_ref = first_financial.get('continuation_authorization')
        if continuation_ref is None:
            continue
        try:
            continuation = Source.model_validate(continuation_ref)
        except (TypeError, ValueError):
            raise FinancialError('financial_continuation_history_invalid') from None
        if continuation.sha256 != _O3_CONTINUATION_SHA256:
            raise FinancialError('financial_continuation_history_invalid')
        anchors.add(key)
    return anchors


def _validate_o3_continuation_history(state: Mapping[str, Any], snapshot: Mapping[str, Any]) -> None:
    """Require the actual closed O1/O2 ledger history before an O3 card can use the shared family."""
    reservations = state.get('reservations')
    if not isinstance(reservations, Mapping):
        raise FinancialError('financial_continuation_history_invalid')
    candidate_authority = snapshot.get('authority', {}).get('sha256')
    candidate_stage = snapshot.get('stage_id')
    candidate_scope = snapshot.get('scope_id')
    candidate_source = snapshot.get('continuation_authorization')
    candidate_exposure_source = snapshot.get('continuation_exposure_amendment')
    if (not isinstance(candidate_authority, str) or not isinstance(candidate_stage, str)
            or not isinstance(candidate_scope, str) or not isinstance(candidate_source, Mapping)
            or candidate_source.get('sha256') != _O3_CONTINUATION_SHA256
            or snapshot.get('batch_id') != _O2_FIXED_FAMILY_BATCH_ID):
        raise FinancialError('financial_continuation_history_invalid')
    if candidate_exposure_source is not None:
        try:
            validate_o4_retry_cap_removal_source(Source.model_validate(candidate_exposure_source))
        except (FinancialError, TypeError, ValueError):
            raise FinancialError('financial_continuation_history_invalid') from None

    family: list[tuple[str, Mapping[str, Any]]] = []
    for reservation_id, record in reservations.items():
        if not isinstance(record, Mapping):
            raise FinancialError('financial_continuation_history_invalid')
        financial = record.get('financial')
        if not isinstance(financial, Mapping):
            raise FinancialError('financial_legacy_liability_unconverted')
        if financial.get('batch_id') == _O2_FIXED_FAMILY_BATCH_ID:
            if financial.get('scope_id') != candidate_scope:
                raise FinancialError('financial_continuation_history_invalid')
            family.append((reservation_id, record))

    groups: dict[tuple[str, str], list[tuple[str, Mapping[str, Any]]]] = {}
    for reservation_id, record in family:
        financial = record['financial']
        authority = financial.get('authority', {})
        authority_sha = authority.get('sha256') if isinstance(authority, Mapping) else None
        stage_id = financial.get('stage_id')
        if not isinstance(authority_sha, str) or not isinstance(stage_id, str):
            raise FinancialError('financial_continuation_history_invalid')
        groups.setdefault((authority_sha, stage_id), []).append((reservation_id, record))

    candidate_key = (candidate_authority, candidate_stage)
    # The EUR48 continuation epoch is deliberately narrower than the fixed
    # family. Historical cards remain in the normal shared-finance/high-water
    # calculation, but only the two pinned O1/O2 cards and O3-bound cards
    # contribute authorized stage allocations to this epoch.
    candidate_rows = groups.get(candidate_key, [])
    base_allocations: list[Decimal] = []
    continuation_allocations: list[Decimal] = []
    all_runs: set[str] = set()

    def card_for_group(key: tuple[str, str], rows: list[tuple[str, Mapping[str, Any]]], *, predecessor: bool):
        authority_sha, stage_id = key
        if (len(rows) != 2 if predecessor else not 1 <= len(rows) <= 2):
            raise FinancialError('financial_continuation_history_invalid')
        first = rows[0][1]['financial']
        authority_ref = Source.model_validate(first['authority'])
        if authority_ref.sha256 != authority_sha:
            raise FinancialError('financial_continuation_history_invalid')
        raw = authority_ref.json()
        card = FinancialAuthorization.model_validate(raw)
        if (card.batch_id != _O2_FIXED_FAMILY_BATCH_ID or card.stage_id != stage_id
                or card.ledger_scope_id != candidate_scope
                or len(card.entries) != 2 or card.approved_run_count != 6
                or {entry.get('priority') for entry in card.entries} != {'P0', 'P1'}
                or {entry.get('config_id'): entry.get('priority') for entry in card.entries} != _O3_CONFIG_PRIORITIES
                or {entry.get('mode') for entry in card.entries} != {'acceptance'}
                or len({entry.get('scheduled_start') for entry in card.entries}) != 1
                or any(entry.get('lifetime_cap_seconds') != 4200
                       or set(entry.get('run_ids', {})) != {'aws', 'azure', 'gcp'}
                       or len(set(entry.get('run_ids', {}).values())) != 3
                       for entry in card.entries)
                or len({run for entry in card.entries for run in entry['run_ids'].values()}) != 6):
            raise FinancialError('financial_continuation_history_invalid')
        is_anchor = key in _O3_EPOCH_ANCHORS
        is_continuation = card.continuation_authorization is not None
        if predecessor and (is_anchor == is_continuation):
            raise FinancialError('financial_continuation_history_invalid')
        if is_continuation and card.continuation_authorization.sha256 != _O3_CONTINUATION_SHA256:
            raise FinancialError('financial_continuation_history_invalid')
        card_exposure_source = card.continuation_exposure_amendment
        if card_exposure_source is not None:
            try:
                validate_o4_retry_cap_removal_source(card_exposure_source)
            except FinancialError:
                raise FinancialError('financial_continuation_history_invalid') from None
        if amount(card.caps.stage_eur) != Decimal('16'):
            raise FinancialError('financial_continuation_history_invalid')
        if (card.caps.project_eur != first['caps'].get('project_eur')
                or card.caps.model_dump(mode='json') != first.get('caps')
                or ((card.continuation_authorization.model_dump(mode='json')
                     if card.continuation_authorization is not None else None)
                    != first.get('continuation_authorization'))
                or ((card_exposure_source.model_dump(mode='json')
                     if card_exposure_source is not None else None)
                    != first.get('continuation_exposure_amendment'))
                or ((card.financial_policy_amendment.model_dump(mode='json')
                     if card.financial_policy_amendment is not None else None)
                    != first.get('financial_policy_amendment'))
                or any(row[1]['financial'].get('batch_id') != card.batch_id
                       or row[1]['financial'].get('stage_id') != card.stage_id
                       or row[1]['financial'].get('authority', {}).get('sha256') != authority_sha
                       or row[1]['financial'].get('caps') != card.caps.model_dump(mode='json')
                       or row[1]['financial'].get('continuation_exposure_amendment')
                          != (card_exposure_source.model_dump(mode='json')
                              if card_exposure_source is not None else None)
                       for row in rows)):
            raise FinancialError('financial_continuation_history_invalid')
        if card.financial_policy_amendment is not None:
            if card.financial_policy_amendment.sha256 != _O2_AMENDMENT_SHA256:
                raise FinancialError('financial_continuation_history_invalid')
        elif ceiling(card.caps.project_eur) > Decimal('300'):
            raise FinancialError('financial_continuation_history_invalid')
        if is_continuation and card.financial_policy_amendment is None:
            raise FinancialError('financial_continuation_history_invalid')

        card_entries = {digest(entry): entry for entry in card.entries}
        seen_campaigns: set[str] = set()
        for reservation_id, record in rows:
            financial = record['financial']
            binding = financial.get('binding')
            if not isinstance(binding, Mapping) or digest(binding) not in card_entries:
                raise FinancialError('financial_continuation_history_invalid')
            campaign_id = binding.get('campaign_id')
            if (not isinstance(campaign_id, str) or campaign_id in seen_campaigns
                    or not isinstance(record.get('reservation_id'), str)
                    or record.get('reservation_id') != reservation_id
                    or record.get('execution_scope') != 'paid_request'
                    or record.get('request', {}).get('campaign_id') != campaign_id
                    or record.get('child_identities') != binding.get('run_ids')
                    or set(record.get('plan', {}).get('providers', [])) != {'aws', 'azure', 'gcp'}):
                raise FinancialError('financial_continuation_history_invalid')
            seen_campaigns.add(campaign_id)
            claim = state.get('campaign_claims', {}).get(campaign_id)
            try:
                from .contracts.admission import ReservationEvidence
                claim_evidence = ReservationEvidence.model_validate(claim['evidence'])
            except (TypeError, ValueError, KeyError):
                raise FinancialError('financial_continuation_history_invalid') from None
            matching_claims = [value for value in state.get('claims', {}).values()
                if isinstance(value, Mapping)
                and value.get('evidence', {}).get('reservation_id', {}).get('value') == reservation_id]
            if (claim_evidence.reservation_id.value != reservation_id
                    or claim_evidence.campaign_id != campaign_id
                    or not matching_claims
                    or any(value.get('fingerprint') != record.get('plan', {}).get('fingerprint')
                           for value in matching_claims)):
                raise FinancialError('financial_continuation_history_invalid')
            runs = set(binding.get('run_ids', {}).values())
            if all_runs & runs:
                raise FinancialError('financial_continuation_history_invalid')
            all_runs.update(runs)
            if predecessor:
                _validate_o3_predecessor_terminal(state, reservation_id, record)
        expected_campaigns = {entry['campaign_id'] for entry in card.entries}
        if ((seen_campaigns != expected_campaigns) if predecessor
                else not seen_campaigns <= expected_campaigns):
            raise FinancialError('financial_continuation_history_invalid')
        return card

    # Detect a pinned anchor appearing under the wrong stage rather than
    # silently treating it as unrelated legacy history.
    for key in groups:
        if key[0] in {anchor[0] for anchor in _O3_EPOCH_ANCHORS} and key not in _O3_EPOCH_ANCHORS:
            raise FinancialError('financial_continuation_history_invalid')
    if not _O3_EPOCH_ANCHORS <= groups.keys():
        raise FinancialError('financial_continuation_history_invalid')
    epoch_groups = _o3_epoch_group_keys(groups, candidate_key)
    for key, rows in groups.items():
        if key == candidate_key or key not in epoch_groups:
            continue
        if key in _O3_EPOCH_ANCHORS:
            card = card_for_group(key, rows, predecessor=True)
            base_allocations.append(amount(card.caps.stage_eur))
        else:
            card = card_for_group(key, rows, predecessor=True)
            continuation_allocations.append(amount(card.caps.stage_eur))
    current_card = card_for_group(candidate_key, candidate_rows, predecessor=False) if candidate_rows else None
    if current_card is not None:
        current_ids = {entry['campaign_id'] for entry in current_card.entries}
        current_snapshot_campaign = snapshot.get('binding', {}).get('campaign_id')
        if current_snapshot_campaign not in current_ids:
            raise FinancialError('financial_continuation_history_invalid')
        if current_card.continuation_authorization is None:
            raise FinancialError('financial_continuation_history_invalid')
        if current_card.continuation_exposure_amendment != (
                Source.model_validate(candidate_exposure_source) if candidate_exposure_source is not None else None):
            raise FinancialError('financial_continuation_history_invalid')
    if len(base_allocations) != 2 or sum(base_allocations, Decimal(0)) != Decimal('32'):
        raise FinancialError('financial_continuation_history_invalid')
    candidate_allocation = amount(snapshot.get('caps', {}).get('stage_eur'))
    retained_new_allocation = sum(base_allocations + continuation_allocations, Decimal(0))
    _validate_o3_epoch_allocation(retained_new_allocation + candidate_allocation,
                                  candidate_allocation,
                                  Source.model_validate(candidate_exposure_source)
                                  if candidate_exposure_source is not None else None)


def _validate_o3_predecessor_terminal(state: Mapping[str, Any], reservation_id: str,
                                      record: Mapping[str, Any]) -> None:
    providers = set(record.get('plan', {}).get('providers', []))
    bills = record.get('provider_billing')
    if (record.get('cleanup_open') is not False or record.get('lifecycle_terminal') is not True
            or reservation_id in state.get('cleanup_open', [])
            or reservation_id in state.get('cleanup_blocked', [])
            or reservation_id in state.get('budget_blocked', [])
            or record.get('provider_cleanup') != {provider: True for provider in providers}
            or not isinstance(bills, Mapping) or set(bills) != providers
            or any(not isinstance(bills.get(provider), Mapping)
                   or type(bills[provider].get('final')) is not bool
                   for provider in providers)):
        raise FinancialError('financial_continuation_predecessor_open')
    events = state.get('events')
    if not isinstance(events, Mapping):
        raise FinancialError('financial_continuation_predecessor_open')
    for provider in providers:
        cleanup_bound = False
        for event_id, event in events.items():
            payload = event.get('payload') if isinstance(event, Mapping) else None
            if not isinstance(payload, Mapping) or payload.get('reservation_id') != reservation_id or payload.get('provider') != provider:
                continue
            expected_digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            if event.get('digest') != expected_digest:
                continue
            try:
                Source(path=payload.get('source_path', ''), sha256=payload.get('source_sha256', '')).read()
            except (FinancialError, ValueError, OSError):
                continue
            if (payload.get('cleanup_verified_absent') is True
                    and payload.get('billable_resources_present') is False
                    and isinstance(payload.get('absence_evidence_id'), str)
                    and payload.get('absence_evidence_id').strip()
                    and isinstance(payload.get('absent_resource_ids'), list)
                    and payload.get('absent_resource_ids')
                    and all(isinstance(item, str) and item.strip()
                            for item in payload.get('absent_resource_ids', []))
                    and len(set(payload.get('absent_resource_ids', []))) == len(payload.get('absent_resource_ids', []))):
                cleanup_bound = True
        # Unknown final invoices remain represented by the original retained
        # commitment and estimate/actual high-water in check_shared_finances.
        # A missing actual bill is not reinterpreted as zero or as a release.
        if not cleanup_bound:
            raise FinancialError('financial_continuation_predecessor_open')


def check_shared_finances(state: dict, snapshot: dict, *, remaining_usd: Decimal) -> None:
    """Called under the SAME exclusive ledger lock as the USD reservation."""
    records = list(state['reservations'].values())
    if any(not isinstance(r.get('financial'), dict) for r in records):
        raise FinancialError('financial_legacy_liability_unconverted')
    for record in records:
        validate_stored_commitment(record, scope_id=state['scope_id'])
    all_prior = [r['financial'] for r in records]
    _validate_o2_batch_identity(all_prior, snapshot)
    if snapshot.get('continuation_authorization') is not None:
        _validate_o3_continuation_history(state, snapshot)
    prior_records = [r for r in records if r.get('financial_terminal_state') != 'cancelled_unused']
    prior = [r['financial'] for r in prior_records]
    records = [r for r in records if r.get('financial_terminal_state') != 'cancelled_unused']
    caps = Caps.model_validate(snapshot['caps'])
    # Never release original maxima or allow later FX changes to discount old exposure.
    rate = max([amount(snapshot['rate'])] + [amount(f['rate']) for f in all_prior])
    gross = [max(amount(f['gross_eur']),
        liability(max(amount(r['reserved_cost_usd']), amount(r.get('incurred_usd', 0))) * rate)
        + max(amount(f['gross_eur']) - liability(amount(r['reserved_cost_usd']) * amount(f['rate'])), Decimal(0)))
             for r, f in zip(records, prior)]
    external = max([amount(snapshot['external_gross_eur'])] + [amount(f['external_gross_eur']) for f in all_prior])
    cash_external = max([amount(snapshot['external_cash_eur'])] + [amount(f['external_cash_eur']) for f in all_prior])
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
    if f.get('continuation_authorization') is not None:
        Source.model_validate(f['continuation_authorization'])
        amendment = f.get('financial_policy_amendment')
        if not isinstance(amendment, Mapping) or amendment.get('sha256') != _O2_AMENDMENT_SHA256:
            raise FinancialError('financial_ledger_invalid')
    exposure_amendment = f.get('continuation_exposure_amendment')
    if exposure_amendment is not None:
        if f.get('continuation_authorization') is None:
            raise FinancialError('financial_ledger_invalid')
        try:
            if Source.model_validate(exposure_amendment).sha256 != _O4_RETRY_CAP_REMOVAL_SHA256:
                raise ValueError('source hash differs')
        except (ValueError, TypeError):
            raise FinancialError('financial_ledger_invalid') from None
    Caps.model_validate(f['caps'])
    if (f['scope_id'] != scope_id or set(f['providers_eur']) != set(record['plan']['providers'])
            or amount(f['rate']) <= 0 or amount(f['cash_eur']) != amount(f['gross_eur'])
            or sum((amount(v) for v in f['providers_eur'].values()), Decimal(0)) != amount(f['gross_eur'])
            or amount(f['gross_eur']) < liability(amount(record['reserved_cost_usd']) * amount(f['rate']))
            or amount(f['external_cash_eur']) != amount(f['external_gross_eur'])):
        raise FinancialError('financial_ledger_invalid')
    terminal = record.get('financial_terminal_state')
    cancellation = record.get('financial_cancellation')
    if terminal is None:
        if cancellation is not None:
            raise FinancialError('financial_ledger_invalid')
    elif terminal in {'cancelled_unused', 'contradicted'}:
        if (not isinstance(cancellation, dict)
                or cancellation.get('status') != terminal
                or cancellation.get('event_id') != f"cancel-unused:{record.get('reservation_id')}"
                or not isinstance(cancellation.get('proof'), dict)
                or not isinstance(cancellation.get('restored_funding_by_bucket'), dict)
                or not cancellation.get('reservation_snapshot_sha256')):
            raise FinancialError('financial_ledger_invalid')
        try:
            for digest in (cancellation.get('proof_sha256', ''), cancellation.get('reservation_snapshot_sha256', '')):
                if (not isinstance(digest, str) or len(digest) != 64
                        or any(ch not in '0123456789abcdef' for ch in digest)):
                    raise ValueError('cancellation hash invalid')
            restored = {k: amount(v) for k, v in cancellation['restored_funding_by_bucket'].items()}
            if (set(restored) != set(record.get('funding_moves', {}))
                    or restored != {k: amount(v) for k, v in record['funding_moves'].items()}
                    or sum(restored.values(), Decimal(0)) != amount(cancellation.get('restored_funding_usd', -1))):
                raise ValueError('restored funding mismatch')
            proof_digest = hashlib.sha256(json.dumps(cancellation['proof'], sort_keys=True).encode()).hexdigest()
            if proof_digest != cancellation['proof_sha256']:
                raise ValueError('cancellation proof digest mismatch')
            artifacts = cancellation['proof'].get('verified_evidence', {}).get('artifacts')
            if (not isinstance(artifacts, list) or len(artifacts) < 3
                    or any(not isinstance(a, dict) or not a.get('path') for a in artifacts)):
                raise ValueError('cancellation artifacts missing')
            for artifact in artifacts:
                digest = artifact.get('sha256')
                if (not isinstance(digest, str) or len(digest) != 64
                        or any(ch not in '0123456789abcdef' for ch in digest)):
                    raise ValueError('cancellation artifact hash invalid')
            if terminal == 'cancelled_unused':
                original = cancellation.get('reservation_snapshot')
                if not isinstance(original, dict):
                    raise ValueError('cancellation original reservation snapshot missing')
                snapshot = hashlib.sha256(json.dumps(original, sort_keys=True,
                    separators=(',', ':')).encode()).hexdigest()
                if snapshot != cancellation['reservation_snapshot_sha256']:
                    raise ValueError('cancellation original reservation snapshot hash mismatch')
                current = dict(record)
                current.pop('financial_terminal_state', None)
                current.pop('financial_cancellation', None)
                current.pop('released_usd', None)
                current.pop('released_by_bucket', None)
                comparable = dict(original)
                comparable.pop('released_usd', None)
                comparable.pop('released_by_bucket', None)
                if current != comparable:
                    raise ValueError('cancelled reservation source fields changed')
                prior_released = amount(original.get('released_usd', '0'))
                prior_buckets = original.get('released_by_bucket', {})
                expected_buckets = {key: amount(value) for key, value in prior_buckets.items()}
                for key, value in restored.items():
                    expected_buckets[key] = expected_buckets.get(key, Decimal(0)) + value
                if (amount(record.get('released_usd', '0')) != prior_released + amount(record['reserved_cost_usd'])
                        or {key: amount(value) for key, value in record.get('released_by_bucket', {}).items()} != expected_buckets):
                    raise ValueError('cancelled reservation release counters changed')
        except (ValueError, TypeError, KeyError) as exc:
            raise FinancialError('financial_ledger_invalid') from exc
    else:
        raise FinancialError('financial_ledger_invalid')


def revalidate_financial_record(record: dict, *, state: dict, now: datetime, end: datetime,
                                quote_freshness_at: datetime | None = None,
                                existing_retrieval: bool = False) -> None:
    if record.get('execution_scope') == 'offline_simulation':
        return
    try:
        financial = record['financial']
        source = financial['authority']
        # A new provider creation claim checks quote age at that claim. Later
        # stages reuse its durable proof so a retained DAY pair can retrieve
        # after 86,400 seconds while source hashes and approval still recheck.
        proofs = record.get('creation_quote_checked_at', {})
        launch_at = (stamp(quote_freshness_at) if quote_freshness_at is not None else
            max((stamp(datetime.fromisoformat(value)) for value in proofs.values()),
                default=stamp(datetime.fromisoformat(record['evidence']['cap_started_at']['value']))))
        validate_authority({'source_path': source['path'], 'source_sha256': source['sha256']},
            binding=financial['binding'], scope_id=financial['scope_id'], now=now, end=end,
            quote_freshness_at=launch_at, existing_authorization=True)
        if (state.get('cleanup_blocked') and not existing_retrieval) or state.get('budget_blocked'):
            raise FinancialError('financial_outstanding_liability')
        # Existing records already include this reservation: add no new exposure.
        check_shared_finances(state, {**financial, 'gross_eur':'0', 'cash_eur':'0',
            'providers_eur':dict.fromkeys(financial['providers_eur'], '0')},
            remaining_usd=sum((amount(v) for v in state['funding_remaining'].values()), Decimal(0)))
        require_transfer_capability(record['plan']['cost_envelopes'])
    except FinancialError:
        raise
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise FinancialError('financial_authorization_invalid') from exc
