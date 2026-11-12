"""Explicitly SYNTHETIC and non-authorizing, created only under pytest temp roots."""
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from cloud_network_benchmark.admission import BudgetLedger, load_cost_inputs
from cloud_network_benchmark.config import resolve_campaign
from cloud_network_benchmark.contracts.common import Provider
from cloud_network_benchmark.financial_controls import financial_binding
from tests.integration.test_f11_ledger import scheduled_request_and_plan

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True))
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def synthetic_finances(tmp_path, count=1, *, paid=False):
    campaign, _ = resolve_campaign(ROOT/'configs/tests/exp-903-three-provider-smoke.yaml', ROOT)
    base = load_cost_inputs(ROOT/'tests/fixtures/f11/cost-inputs.json')
    specs = []
    for i in range(count):
        req, plan = scheduled_request_and_plan(campaign, base, campaign_id=f'SYNTHETIC-NO-LAUNCH-{i}',
            providers=list(Provider), child_prefix=f'SYNTHETIC-{i}', namespace_prefix=f'SYNTHETIC-ns-{i}',
            funding={'primary':'0.5'}, measurement_seconds=76)
        specs.append((req, plan))
    payload = dict(base.payload)
    payload.update(prices_checked_on='2026-11-10', refresh_required_before_paid_execution=False,
                   funding={'primary':str(count/2)})
    rows = {p:{**{k:v for k,v in e.items() if k not in {'source_path','source_sha256'}},
               'source_kind':'provider-price-input', 'priced_on':'2026-11-10'} for p,e in specs[0][1].cost_envelopes.items()}
    if paid:
        for row in rows.values():
            row['result_transfer'] = {'payload_limit_bytes': 1000,
                'protocol_overhead_allowance_bytes': 1000,
                'protocol_overhead_priced_usd': '0.001'}
    payload['provider_run_envelope_templates'] = {campaign.config_id:{p:{**{k:v for k,v in e.items() if k != 'run_id'},
        } for p,e in rows.items()}}
    payload.pop('provider_run_envelopes', None)
    costfile = tmp_path/'SYNTHETIC-prices.json'; save(costfile, payload)
    costs = load_cost_inputs(costfile)
    runtimefile = save(tmp_path/'SYNTHETIC-runtime.json', {'synthetic':'never-authorize'})
    binding = {'implementation_revision':'c'*40, 'initialized_revisions':['c'*40],
        'runtime_source':runtimefile, 'effective_runtime_sha256':'d'*64, 'sources':[runtimefile]}
    plans = []
    for req, plan in specs:
        envs = {p:{**e, **({'result_transfer':rows[p]['result_transfer']} if paid else {}),
            'run_id':plan.child_identities[p], 'source_kind':'provider-price-input', 'priced_on':'2026-11-10',
            'source_path':str(costfile), 'source_sha256':costs.payload_sha256} for p,e in plan.cost_envelopes.items()}
        support = {p:{'source_path':str(costfile), 'source_sha256':costs.payload_sha256,
            'included_usd':e['components']['expiry_cleanup_support']['amount_usd']} for p,e in envs.items()}
        support['aws'].update(expiry_input_id='SYNTHETIC', expiry_input_sha256=payload['provider_support']['aws']['sha256'],
                              expiry_cost_regions=rows['aws']['regions'])
        req = req.model_copy(update={'source_path':str(costfile), 'source_sha256':costs.payload_sha256})
        # Unknown active totals must be derived by ledger; never lie with a stale zero.
        from cloud_network_benchmark.contracts.common import Evidence
        req = req.model_copy(update={'active_reserved_usd':Evidence[float](unavailable_reason='ledger derives')})
        if not paid:
            synthetic_rows = {p:{**{k:v for k,v in e.items() if k not in {'source_path','source_sha256'}},
                'source_kind':'synthetic-test-only'} for p,e in envs.items()}
            envsrc = save(tmp_path/f'SYNTHETIC-envelopes-{req.campaign_id}.json',
                {'schema_version':1, 'fixture_scope':'synthetic-test-only', 'provider_run_envelopes':synthetic_rows})
            envs = {p:{**e, 'source_path':envsrc['path'], 'source_sha256':envsrc['sha256']} for p,e in synthetic_rows.items()}
        plans.append((req, replace(plan, costs=costs, cost_envelopes=envs,
            execution_scope='paid_request' if paid else 'offline_simulation',
            provider_support=support, financial_binding=binding)))
    checks = {'checked_at':NOW.isoformat(), 'valid_until':'2026-11-11T00:00:00+00:00',
              'official_urls':['https://example.invalid/SYNTHETIC-NEVER-AUTHORIZE']}
    fxdata = {**checks, 'currency':'USD', 'budget_currency':'EUR', 'conservative_eur_per_unit':'1.01'}
    fx = {**save(tmp_path/'SYNTHETIC-fx.json', fxdata), **fxdata}
    other = save(tmp_path/'SYNTHETIC-external.json', {'ledger_scope_id':'SYNTHETIC-OFFLINE', 'currency':'EUR',
        'gross_eur':'0', 'personal_cash_eur':'0', 'excludes_this_ledger':True})
    card = {'schema_version':1, 'kind':'explicit-human-paid-approval', 'evidence_id':'SYNTHETIC-NONAUTHORIZING',
        'approved_by':'SYNTHETIC-NON-HUMAN-TEST', 'approved_at':NOW.isoformat(), 'valid_from':NOW.isoformat(),
        'valid_until':checks['valid_until'], 'currency':'EUR', 'ledger_scope_id':'SYNTHETIC-OFFLINE',
        'batch_id':'SYNTHETIC-BATCH', 'stage_id':'SYNTHETIC-STAGE', 'approved_run_count':count*len(Provider),
        'entries':[financial_binding(p,r) for r,p in plans],
        'caps':{'project_eur':'300', 'working_eur':'240', 'protected_eur':'60', 'personal_cash_eur':'240',
            'run_eur':'10', 'batch_eur':'240', 'stage_eur':'240', 'providers_eur':dict.fromkeys(rows,'75')},
        'price':{**save(costfile,payload), **checks}, 'fx':fx, 'external_liabilities':{**other, **checks},
        'other_gross_eur':'0', 'other_personal_cash_eur':'0', 'fx_card_fee_rate':'0.01', 'fixed_fees_eur':'0.01',
        'funding_treatment':'full-personal-cash-no-offsets'}
    path = tmp_path/'SYNTHETIC-card.json'
    reference = save(path, card)
    evidence = {'evidence_id':'SYNTHETIC', 'source_path':reference['path'], 'source_sha256':reference['sha256']}
    plans = [(r, replace(p, prerequisites={k:evidence for k in ('paid_authorization','f10_offline','quota_review')})) for r,p in plans]
    book = BudgetLedger(tmp_path/'SYNTHETIC-ledger.json', scope_id='SYNTHETIC-OFFLINE',
        utc_now=lambda:NOW, monotonic_now=lambda:1., controller_session_id='SYNTHETIC-SESSION')
    book.initialize(costs)
    return book, plans, card, path


def rewrite_card(plans, path, card):
    source = save(path, card)
    reference = {'evidence_id':'SYNTHETIC', 'source_path':source['path'], 'source_sha256':source['sha256']}
    return [(r, replace(p, prerequisites={**p.prerequisites, 'paid_authorization':reference})) for r,p in plans]
