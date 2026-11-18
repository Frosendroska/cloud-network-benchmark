"""Isolated credential-free affected delta. Never reads cloud credentials or runs a provider."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

TESTS = [
    'tests/integration/test_f11_ledger.py',
    'tests/integration/test_financial_admission.py',
    'tests/unit/test_f09_lifecycle.py',
    'tests/unit/test_f09_runtime_config.py',
    'tests/unit/test_f09_cli.py',
    'tests/integration/test_pre_vm_failure_recovery.py',
    'tests/integration/test_azure_sweden.py',
    'tests/integration/test_azure_provider_flow.py',
    'tests/contract/test_azure_provider_contract.py',
    'tests/integration/test_admission_factory_recovery.py',
    'tests/integration/test_f09_multi_provider_factory.py::test_default_gcp_factory_protects_provisions_and_cleans_up_with_source_shaped_commands',
    'tests/integration/test_f09_multi_provider_factory.py::test_default_azure_factory_uses_observed_cleanup_runner_for_provision_and_cleanup',
    'tests/integration/test_f09_multi_provider_factory.py::test_default_three_provider_factory_reserves_and_enforces_global_claim_barrier[protection-failure]',
    'tests/integration/test_f09_multi_provider_factory.py::test_default_three_provider_factory_reserves_and_enforces_global_claim_barrier[receipt-before-f11-recovery]',
]
SOURCES = ['src/cloud_network_benchmark/'+p for p in (
    'admission.py','azure_provider.py','lifecycle_runtime.py','orchestration.py','lifecycle_cleanup.py')]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--revision',required=True)
    parser.add_argument('--thesis',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    source=args.source.resolve(strict=True); out=args.out.absolute()
    if out.exists() or out.is_symlink(): raise ValueError('evidence destination must be new')
    if source in out.parents: raise ValueError('evidence must remain outside source')
    out.mkdir(mode=0o700,parents=True)
    home=out/'empty-home';home.mkdir(mode=0o700)
    # Intentionally do not inherit HOME, authentication variables or provider
    # configuration. Tests replace provider commands; Git/Thesis reads are local.
    env={'HOME':str(home),'PATH':'/usr/local/bin:/usr/bin:/bin',
        'THESIS_ROOT':str(args.thesis.resolve(strict=True)),
        'PYTHONPATH':str(source/'src')+':'+str(source),
        'PYTHONDONTWRITEBYTECODE':'1','TMPDIR':str(out),
        'AWS_EC2_METADATA_DISABLED':'true','MPLBACKEND':'Agg'}
    rows=[]
    def run(name,argv,timeout=180):
        start=time.monotonic()
        p=subprocess.run(argv,cwd=source,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
        log=out/(name+'.log');log.write_bytes(p.stdout)
        row={'name':name,'exit_code':p.returncode,'seconds':round(time.monotonic()-start,3),
            'sha256':hashlib.sha256(p.stdout).hexdigest()}
        rows.append(row);print(json.dumps(row),flush=True)
        return p
    revision=run('revision',['git','rev-parse','HEAD']).stdout.decode().strip()
    before=run('source-before',['git','status','--porcelain']).stdout
    if revision!=args.revision or before: raise ValueError('source must be clean at exact reviewed revision')
    run('python-version',[sys.executable,'--version'])
    run('affected-pytest',[sys.executable,'-m','pytest','-q','-p','no:cacheprovider',*TESTS,
        '-k','not runbook_fake_process_retries_late_and_creating_targets_without_controller',
        '--junitxml='+str(out/'pytest.xml')])
    run('diff-check',['git','diff','--check'])
    after=run('source-after',['git','status','--porcelain']).stdout
    tree=ET.parse(out/'pytest.xml')
    counts={k:sum(int(s.get(k,'0')) for s in tree.iter('testsuite')) for k in ('tests','failures','errors','skipped')}
    summary={'revision':revision,'checks':rows,'pytest':counts,'source_clean_before_after':before==after==b'',
        'source_sha256':{p:hashlib.sha256((source/p).read_bytes()).hexdigest() for p in SOURCES},
        'environment_keys':sorted(env),'scope':'affected Python admission/clock/factory/recovery delta only',
        'complete':before==after==b'' and all(r['exit_code']==0 for r in rows)
            and counts['tests']==246 and not any(counts[k] for k in ('failures','errors','skipped'))}
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    hashes={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(out.iterdir()) if p.is_file()}
    (out/'sha256.json').write_text(json.dumps(hashes,indent=2)+'\n')
    archive=out.with_suffix('.zip')
    if archive.exists(): raise FileExistsError('archive destination must be new')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(out.iterdir()):
            if p.is_file():z.write(p,p.name)
    print(json.dumps({'complete':summary['complete'],'pytest':counts,'archive':str(archive),
        'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}),flush=True)
    return 0 if summary['complete'] else 1

if __name__=='__main__':raise SystemExit(main())
