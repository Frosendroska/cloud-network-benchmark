"""Isolated credential-free affected delta. Never reads cloud credentials or runs a provider."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

TESTS = [
    'tests/integration/test_provider_precreation_boundaries.py',
    'tests/unit/test_aws_provider.py', 'tests/unit/test_gcp_provider.py',
    'tests/unit/test_azure_provider.py', 'tests/unit/test_azure_saved_plan.py',
    'tests/integration/test_aws_provider_sequences.py',
    'tests/integration/test_gcp_provider_sequences.py',
    'tests/integration/test_azure_provider_flow.py',
    'tests/integration/test_azure_sweden.py',
    'tests/contract/test_azure_provider_contract.py',
    'tests/integration/test_admission_factory_recovery.py',
    'tests/integration/test_pre_vm_failure_recovery.py',
    'tests/integration/test_f09_multi_provider_factory.py::test_default_gcp_factory_protects_provisions_and_cleans_up_with_source_shaped_commands',
    'tests/integration/test_f09_multi_provider_factory.py::test_default_azure_factory_uses_observed_cleanup_runner_for_provision_and_cleanup',
    'tests/integration/test_f09_multi_provider_factory.py::test_default_three_provider_factory_reserves_and_enforces_global_claim_barrier[protection-failure]',
    'tests/integration/test_f09_multi_provider_factory.py::test_default_three_provider_factory_reserves_and_enforces_global_claim_barrier[receipt-before-f11-recovery]',
]
SOURCES = ['src/cloud_network_benchmark/'+p for p in (
    'providers/aws.py','providers/gcp.py','azure_provider.py','azure_plan_scope.py')]
SOURCES += ['tests/integration/test_provider_precreation_boundaries.py',
    'docs/validation/check-provider-precreation-boundaries.py']
SOURCES += ['terraform/azure/'+root+'/'+name for root in ('expiry','pair')
    for name in ('versions.tf','.terraform.lock.hcl')]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--revision',required=True)
    parser.add_argument('--thesis',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--terraform',type=Path,required=True)
    parser.add_argument('--mirror',type=Path,required=True)
    parser.add_argument('--expected-tests',type=int,required=True)
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
    mirror=args.mirror.resolve(strict=True)
    terraform=args.terraform.resolve(strict=True)
    config=out/'mirror.rc'
    config.write_text('provider_installation {\n filesystem_mirror {\n  path = '+json.dumps(str(mirror))+'\n }\n}\n')
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
    native=[]
    for name in ('expiry','pair'):
        original=source/'terraform/azure'/name
        root=out/'terraform'/name
        root.mkdir(parents=True)
        lock_before=(original/'.terraform.lock.hcl').read_bytes()
        for path in original.glob('*.tf'):
            if path.is_symlink(): raise ValueError('unexpected source symlink')
            shutil.copy2(path,root/path.name)
        shutil.copy2(original/'.terraform.lock.hcl',root/'.terraform.lock.hcl')
        shutil.copytree(original/'tests',root/'tests')
        env.update(TF_CLI_CONFIG_FILE=str(config),TF_IN_AUTOMATION='1',
            TF_DATA_DIR=str(out/'terraform-data'/name),CHECKPOINT_DISABLE='1')
        prefix=[str(terraform),'-chdir='+str(root)]
        run(name+'-fmt',prefix+['fmt','-check','-recursive'])
        run(name+'-init',prefix+['init','-input=false','-lockfile=readonly'])
        run(name+'-validate',prefix+['validate','-no-color'])
        p=run(name+'-mock-plan',prefix+['test','-json','-no-color'])
        messages=[json.loads(line) for line in p.stdout.splitlines() if line.strip()]
        summaries=[m['test_summary'] for m in messages if m.get('type')=='test_summary']
        passed=bool(summaries and summaries[-1].get('status')=='pass'
            and not any(summaries[-1].get(k,0) for k in ('failed','errored','skipped')))
        native.append({'root':name,'summary':summaries,'complete':passed,
            'lock_unchanged':lock_before==(root/'.terraform.lock.hcl').read_bytes()})
    run('diff-check',['git','diff','--check'])
    after=run('source-after',['git','status','--porcelain']).stdout
    tree=ET.parse(out/'pytest.xml')
    counts={k:sum(int(s.get(k,'0')) for s in tree.iter('testsuite')) for k in ('tests','failures','errors','skipped')}
    summary={'revision':revision,'checks':rows,'pytest':counts,'source_clean_before_after':before==after==b'',
        'source_sha256':{p:hashlib.sha256((source/p).read_bytes()).hexdigest() for p in SOURCES},
        'environment_keys':sorted(env),'scope':'affected provider precreation/recovery Python delta and eight Azure native mocked plans only',
        'terraform':native,'terraform_version':run('terraform-version',[str(terraform),'version','-json']).stdout.decode(),
        'mirror':str(mirror),
        'complete':before==after==b'' and all(r['exit_code']==0 for r in rows)
            and all(n['complete'] and n['lock_unchanged'] for n in native)
            and counts['tests']==args.expected_tests and not any(counts[k] for k in ('failures','errors','skipped'))}
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
