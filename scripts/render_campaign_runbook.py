#!/usr/bin/env python3
"""Render the offline operator HTML from its Markdown source; no third-party packages."""
from __future__ import annotations
import argparse
import hashlib
import html
from pathlib import Path
import re

STAGES = [
    ('accounts', 'Accounts & controller', 'Prepare', 'Choose the host, accounts and funding before installing anything.', ['Obtain the TUM VM, SSH access, persistent storage and stable outbound IPv4.', 'Record all three account IDs; verify credits, regional quotas and expiry permissions.', 'Review host, identities, funding and permissions with Codex.']),
    ('tests', 'Install & test', 'Runner · offline', 'Keep the test evidence. A passing local suite precedes paid acceptance.', ['Install Python 3.12, Terraform, cloud CLIs and PowerShell 7.', 'Run the complete Python suite, schema check, six Terraform roots and four mock suites.', 'Review actual Ubuntu results and unexpected skips; retain revision and logs.']),
    ('identity', 'Credentials & service', 'Runner', 'One private operator file, renewing identities and one persistent supervisor.', ['Store credentials and the benchmark SSH key outside Git/results.', 'Fill the operator YAML from actual IDs, prices, allowances and evidence.', 'Verify CLI and Terraform renewal in the actual service environment.', 'Enable systemd linger; arrange persistent logs and private recovery-state backup.']),
    ('acceptance', 'Supervised acceptance', 'Paid · supervised', 'Prove real collection, placement, restart, expiry and complete deletion.', ['Review check output, native test logs, quotes and paid authorization with Codex.', 'Inspect V-S1 and V-S3 native output and actual placement membership.', 'Supervise V-A-FAIL, V-Z-FAIL and V-G-FAIL; preserve expiry and full-cleanup evidence.', 'Verify SSH disconnect/supervisor recovery without duplicate measurement.']),
    ('calibration', 'Calibrate common N', 'Runner → laptop', 'Measure once, analyse locally and freeze the shared stream count.', ['Review acceptance and cleanup before authorizing calibration.', 'Collect all three retained-pair calibration results and run recommend-common-n locally.', 'Review/freeze N with Codex; measure growth, guest free space and verified export capacity.']),
    ('day', 'Continuous DAY', 'Paid · 24 hours', 'A separate retained pair per cloud; review coverage before scheduling WEEK.', ['Review N, full-workflow reservation, disk/export fit and the future DAY start.', 'Run the continuous day; retrieve and checksum-verify native evidence after measurement.', 'Review full TCP duration, RTT coverage, costs and four UTC windows with Codex.']),
    ('week', 'Run & watch WEEK', 'Paid · 7 days', 'P0 and P1 share the windows, with independent resources and one budget.', ['Review feasible seven-day dates and exact UTC windows; preserve the full protocol.', 'Start WEEK once; inspect the first slot and service behavior.', 'After each expected slot, check counts, cleanup, exports, backups and free space.']),
    ('analyse', 'Download & write', 'Laptop · during WEEK', 'Inspect closed observations now, while later slots keep running.', ['Copy only verified closed exports; verify native and export checksums locally.', 'Produce dated individual/interim reports with selected IDs and missing/failed counts.', 'Draft provisional figures and prose; keep N/windows fixed and retain originals.']),
    ('finish', 'Reconcile & finish', 'Runner + laptop', 'Close the expected sample, resources, costs and research provenance.', ['Resolve every cleanup liability using original private recovery state.', 'Verify final downloads/second copies, all failed/missed slots and actual costs.', 'Review final reports with Codex and update Thesis run references/canonical findings.', 'Confirm full experiment-resource absence; review any replacement or P2/P3 separately.']),
]
COMMAND_LABELS = [
    'Laptop · connect to the runner', 'Runner · install once', 'Runner · complete offline/native checks',
    'Runner · authenticate with reviewed identities', 'Runner · settings and systemd service',
    'Runner · restore shell context and check', 'Runner · acceptance', 'Runner · supervised failure continuation',
    'Runner · calibration', 'Laptop · recommend common N', 'Runner · DAY', 'Runner · WEEK',
    'Runner · daily and per-slot inspection', 'Laptop · download, verify and analyse one closed entry',
    'Laptop · combine verified closed entries', 'Runner · cleanup from original private state',
]

def inline(text: str) -> str:
    saved = []
    def token(match):
        raw = match.group(0)
        if raw.startswith('`'):
            value = '<code>' + html.escape(raw[1:-1]) + '</code>'
        else:
            label, url = re.fullmatch(r'\[([^\]]+)\]\(([^)]+)\)', raw).groups()
            value = '<a href="' + html.escape(url, quote=True) + '">' + html.escape(label) + '</a>'
        saved.append(value)
        return f'\x00{len(saved)-1}\x00'
    text = re.sub(r'`[^`]+`|\[[^\]]+\]\([^)]+\)', token, text)
    text = html.escape(text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    for i, value in enumerate(saved):
        text = text.replace(f'\x00{i}\x00', value)
    return text


def render_markdown(text: str, counter: list[int]) -> str:
    lines = text.strip().splitlines()
    result = []; i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip(): i += 1; continue
        if line.startswith('```'):
            i += 1; command = []
            while i < len(lines) and not lines[i].startswith('```'):
                command.append(lines[i]); i += 1
            i += 1; raw = '\n'.join(command); n = counter[0]; counter[0] += 1
            title = COMMAND_LABELS[n]
            risk = 'Live cleanup' if 'lifecycle cleanup' in raw else 'Paid · review first' if '--execute' in raw else 'Commands'
            result.append(f'<div class="command"><div class="command-head"><span>{title}</span><div><span class="command-kind">{risk}</span><button type="button" class="copy" data-command="cmd-{n}" aria-label="Copy {title}">Copy</button></div></div><pre tabindex="0"><code id="cmd-{n}" data-original="{html.escape(raw, quote=True)}">{html.escape(raw)}</code></pre></div>')
            continue
        if line.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].startswith('|'):
                cells = [c.strip() for c in lines[i].strip('|').split('|')]
                if not all(re.fullmatch(r'[-: ]+', c) for c in cells): rows.append(cells)
                i += 1
            head, *body = rows
            result.append('<div class="table-wrap"><table><thead><tr>' + ''.join('<th>'+inline(c)+'</th>' for c in head) + '</tr></thead><tbody>' + ''.join('<tr>'+''.join('<td>'+inline(c)+'</td>' for c in row)+'</tr>' for row in body) + '</tbody></table></div>')
            continue
        if re.match(r'^\d+\. ', line):
            items = []
            while i < len(lines) and re.match(r'^\d+\. ', lines[i]):
                items.append(re.sub(r'^\d+\. ', '', lines[i])); i += 1
            result.append('<ol>' + ''.join('<li>'+inline(x)+'</li>' for x in items) + '</ol>'); continue
        para = [line]; i += 1
        while i < len(lines) and lines[i].strip() and not lines[i].startswith(('```', '|')) and not re.match(r'^\d+\. ', lines[i]):
            para.append(lines[i]); i += 1
        value = ' '.join(para)
        cls = ' class="review"' if 'Ask Codex' in value or value.startswith('**Review ') else ''
        result.append('<p'+cls+'>'+inline(value)+'</p>')
    return '\n'.join(result)

CSS = r'''
:root{--bg:#f5f4ef;--paper:#fff;--ink:#1b302e;--muted:#5c6b67;--line:#dce3dc;--green:#19614d;--soft:#eaf2e9;--orange:#92511e;--amber:#fff1df;--mono:ui-monospace,SFMono-Regular,Consolas,monospace;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--ink);background:var(--bg);font-size:15px;line-height:1.6}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:24px}body{margin:0}a{color:var(--green);text-underline-offset:3px}button,input,select{font:inherit}button{cursor:pointer}button:focus-visible,a:focus-visible,summary:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #d29b3b;outline-offset:3px}button{border:1px solid var(--line);border-radius:8px;background:white;color:var(--ink);padding:7px 12px}button:hover{border-color:var(--green)}.shell{max-width:1480px;margin:auto;display:grid;grid-template-columns:260px minmax(0,1fr);gap:38px;padding:30px 36px 70px}.sidebar{position:sticky;top:26px;height:calc(100vh - 52px);overflow:auto;padding:6px 4px 24px 0}.brand{font-size:12px;letter-spacing:.13em;text-transform:uppercase;font-weight:750;color:var(--green)}.sidebar h2{font-size:20px;line-height:1.2;margin:12px 0 22px}.progress-track{height:6px;background:#dce5d9;border-radius:10px;overflow:hidden;margin:10px 0}.progress-fill{height:100%;width:0;background:var(--green);transition:width .2s}.muted,.small{color:var(--muted)}.small{font-size:12px}.route{list-style:none;padding:0;margin:22px 0}.route a{display:flex;align-items:center;gap:11px;text-decoration:none;color:var(--muted);padding:10px 8px;border-radius:9px;font-size:13px;line-height:1.25}.route a:hover,.route a.active{background:var(--soft);color:var(--green)}.route .number{border:1px solid #cad5ca;width:26px;height:26px;border-radius:50%;display:grid;place-items:center;font-family:var(--mono);font-size:11px;flex-shrink:0}.route a.done .number{background:var(--green);color:#fff;border-color:var(--green)}.side-note{border-top:1px solid var(--line);padding-top:18px;font-size:12px;color:var(--muted)}.side-note a{display:block;margin:8px 0}.hero{padding:5px 0 22px}.eyebrow{font:700 11px/1.4 var(--mono);letter-spacing:.1em;color:var(--green);text-transform:uppercase}h1{font-size:clamp(32px,4vw,52px);letter-spacing:-.045em;line-height:1.08;max-width:740px;margin:15px 0 20px}.lede{font-size:17px;color:var(--muted);max-width:740px}.badges{display:flex;flex-wrap:wrap;gap:8px;margin:22px 0}.badge{font:600 11px/1.5 var(--mono);padding:6px 10px;border-radius:30px;background:var(--soft);color:var(--green)}.badge.warn{background:var(--amber);color:var(--orange)}.boundary{border-left:3px solid #bd8950;padding:10px 16px;background:#faf0e3;font-size:13px;border-radius:0 8px 8px 0}.next{display:flex;align-items:center;justify-content:space-between;gap:20px;background:var(--ink);color:#fff;padding:20px 24px;border-radius:14px;margin:22px 0}.next .eyebrow{color:#aacfb4}.next p{margin:4px 0 0;font-size:14px}.next a{flex-shrink:0;background:#ecf3e6;color:var(--ink);padding:9px 13px;border-radius:8px;font-size:12px;text-decoration:none;font-weight:650}.panel{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:22px 24px;margin:18px 0}.panel h2{font-size:20px;margin:0 0 6px;letter-spacing:-.02em}.panel p{margin:6px 0 15px;font-size:13px;color:var(--muted)}.fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-top:20px}label.field{display:flex;flex-direction:column;font-size:12px;font-weight:650;gap:5px}label.field small{font-weight:400;font-size:11px;color:var(--muted)}input[type=text],input[type=date],input[type=time],select{min-width:0;width:100%;padding:9px 10px;border:1px solid #cfd8cf;border-radius:7px;background:#fafbf8;color:var(--ink);font:12px/1.5 var(--mono)}input:invalid{border-color:#b4553c}.field-group{border:0;border-top:1px solid var(--line);padding:18px 0 0;margin:22px 0 0;min-width:0}.field-group summary{font-size:13px;cursor:pointer;color:var(--green);font-weight:650}.field-group .fields{margin-top:12px}.control-note{font-size:12px!important;margin-bottom:0!important}.toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:30px 0 14px}.toolbar h2{font-size:23px;margin:0}.toolbar div{display:flex;gap:7px}.toolbar button{font-size:12px}.stage{background:white;border:1px solid var(--line);border-radius:14px;margin:12px 0;overflow:hidden;scroll-margin-top:20px}.stage>summary{list-style:none;display:flex;align-items:flex-start;gap:17px;padding:24px;cursor:pointer}.stage>summary::-webkit-details-marker{display:none}.step-index{font:15px/1 var(--mono);background:var(--soft);color:var(--green);border-radius:10px;padding:13px;margin-top:2px}.stage.done .step-index{background:var(--green);color:#fff}.stage-title{flex:1;min-width:0}.stage-title h3{font-size:20px;letter-spacing:-.02em;margin:0}.stage-title p{color:var(--muted);font-size:13px;margin:6px 0 0}.stage-kind{font:10px/1.3 var(--mono);color:var(--green);text-transform:uppercase;letter-spacing:.07em}.stage-caret{color:var(--muted);font-size:18px;margin-top:7px}.stage[open] .stage-caret{transform:rotate(90deg)}.stage-body{border-top:1px solid var(--line);padding:0 24px 24px}.stage-body p,.stage-body li{font-size:14px}.stage-body li{margin:9px 0}.stage-body p{margin:20px 0}.stage-body .review{border:1px solid #d6e5cc;background:#f1f7ed;border-radius:9px;padding:13px 15px;color:#28452b}.command{margin:20px 0;border:1px solid #273c38;border-radius:10px;background:#142b27;overflow:hidden;color:#e3eee7}.command-head{background:#203a34;padding:9px 13px;display:flex;gap:10px;align-items:center;justify-content:space-between;font:11px/1.5 var(--mono);color:#c0d3c9}.command-head>div{display:flex;align-items:center;gap:10px}.command-kind{font-size:10px;color:#c4d3c5}.command button{background:#304d42;color:#f1f6ee;border-color:#516b5b;font:11px/1.3 var(--mono);padding:5px 9px}.command button:disabled{opacity:.5;cursor:not-allowed}pre{font:12px/1.7 var(--mono);margin:0;padding:18px;overflow:auto;tab-size:2}p code,li code,td code,.boundary code{font:12px/1.6 var(--mono);background:#f0f3ed;border-radius:4px;padding:2px 4px;overflow-wrap:anywhere}.checklist{background:#f7f9f4;padding:18px;border-radius:10px;margin-top:24px}.checklist h4{font-size:12px;text-transform:uppercase;letter-spacing:.07em;margin:0 0 12px;color:var(--green)}.todo{display:flex;align-items:flex-start;gap:10px;font-size:13px;margin:10px 0;cursor:pointer}.todo input{width:16px;height:16px;margin:4px 0 0;accent-color:var(--green);flex-shrink:0}.todo:has(input:checked){color:var(--muted)}.todo:has(input:checked) span{text-decoration:line-through;text-decoration-color:#b6c9ad}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:12px;margin:20px 0}th,td{text-align:left;vertical-align:top;padding:12px;border-bottom:1px solid var(--line)}th{background:#f0f5ed;color:var(--green);font-size:11px;text-transform:uppercase;letter-spacing:.04em}.finish-map{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin-top:18px}.finish-map strong{display:block;font-size:13px;margin-bottom:5px}.finish-map span{font-size:12px;color:var(--muted)}footer{font-size:11px;color:var(--muted);padding:24px 0;overflow-wrap:anywhere}.toast{position:fixed;right:24px;bottom:24px;background:#1b302e;color:white;box-shadow:0 6px 24px #0002;padding:12px 18px;border-radius:9px;font-size:13px;z-index:10}.toast:empty{display:none}.skip{position:absolute;left:-999px}.skip:focus{left:12px;top:10px;background:white;padding:12px;z-index:20}noscript p{padding:16px;border:1px solid var(--line);background:white}
@media(max-width:1100px){.shell{grid-template-columns:215px minmax(0,1fr);gap:24px;padding:24px}.fields{grid-template-columns:repeat(2,minmax(0,1fr))}.command-head{align-items:flex-start}.command-head>div{flex-shrink:0}.command-kind{display:none}}
@media(max-width:760px){.shell{display:block;padding:18px}.sidebar{position:static;height:auto;overflow:visible;padding:0}.sidebar h2,.sidebar .route,.side-note{display:none}.sidebar .progress-track{max-width:100%}.hero{padding-top:20px}h1{font-size:37px}.panel,.stage>summary{padding:18px}.stage-body{padding:0 16px 18px}.fields{grid-template-columns:1fr 1fr;gap:12px}.next{padding:17px;align-items:flex-start}.next a{font-size:11px}.finish-map{grid-template-columns:1fr}.toolbar h2{font-size:20px}.stage-title h3{font-size:18px}.stage-kind{font-size:9px}.command-head{font-size:10px}.command-head>span{max-width:70%}}
@media(max-width:420px){.fields{grid-template-columns:1fr}.next{display:block}.next a{display:inline-block;margin-top:14px}.toolbar{align-items:flex-start}.toolbar div{flex-direction:column}.step-index{padding:10px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}.progress-fill{transition:none}}
@media print{body{background:white}.shell{display:block;padding:0}.sidebar,.copy,.toolbar button,.next a,.fields,.field-group,.toast{display:none}.stage,.panel{break-inside:avoid}.stage-body{display:block!important}.stage>summary{padding:15px}.command{background:white;color:black;border:1px solid #ccc}.command-head{background:#eee;color:black}pre{white-space:pre-wrap;overflow-wrap:anywhere}.stage-caret{display:none}a{color:black}}
'''

JS = r'''
'use strict';
const storageKey = 'cnb-operator-runbook-v1';
const inputs = [...document.querySelectorAll('[data-knob]')];
const checks = [...document.querySelectorAll('.todo input')];
let state = {knobs: {}, checked: []};
let storageAvailable = true;
try { const saved = JSON.parse(localStorage.getItem(storageKey) || 'null'); if (saved && typeof saved === 'object') state = {knobs: saved.knobs || {}, checked: Array.isArray(saved.checked) ? saved.checked : []}; } catch { storageAvailable = false; }
function save(){try {localStorage.setItem(storageKey, JSON.stringify(state));} catch { storageAvailable = false; } document.getElementById('save-note').textContent = storageAvailable ? 'Saved only in this browser. No secrets, uploads or connection to the runner.' : 'Browser storage unavailable: progress lasts only until this page closes.';}
function notify(message){const target=document.getElementById('toast');target.textContent=message;clearTimeout(notify.timer);notify.timer=setTimeout(()=>{target.textContent='';},3500);}
function validateInputs(){inputs.forEach(input=>{if(input.type!=='text')return;const value=input.value;const pattern=input.id==='ssh-user'?/^[A-Za-z_][A-Za-z0-9_-]*$/:/^[A-Za-z0-9][A-Za-z0-9._-]*$/;input.setCustomValidity(!value || pattern.test(value)?'':'Use only letters, numbers, dots, underscores or hyphens; SSH users must start with a letter or underscore.');});}
function substitutions(){return Object.fromEntries(inputs.filter(x=>x.dataset.token && x.validity.valid && x.value.trim()).map(x=>[x.dataset.token,x.value.trim()]));}
function updateCommands(){validateInputs();const replacements=substitutions();const invalid=inputs.some(x=>!x.validity.valid);document.querySelectorAll('code[data-original]').forEach(code=>{let value=code.dataset.original;for(const [token,replacement] of Object.entries(replacements)) value=value.split(token).join(replacement);code.textContent=value;});document.querySelectorAll('.copy').forEach(button=>{button.disabled=invalid;button.title=invalid?'Correct the highlighted setup value before copying.':'Copy text only; this page never executes commands.';});document.getElementById('input-note').textContent=invalid?'Correct the highlighted value. Copying is paused to avoid malformed commands.':'Filled setup values replace matching command placeholders. Blank values remain visibly marked.';}
function updateProgress(){state.checked=checks.filter(x=>x.checked).map(x=>x.id);const count=state.checked.length,total=checks.length;document.getElementById('progress-label').textContent=`${count} of ${total} tasks marked`;document.getElementById('progress-fill').style.width=`${count/total*100}%`;const next=checks.find(x=>!x.checked);const link=document.getElementById('next-link');document.getElementById('next-task').textContent=next?next.dataset.label:'Checklist marked complete. Reconcile actual evidence and remaining resources before calling the study finished.';link.href=next?'#'+next.closest('.stage').id:'#finish';link.textContent=next?'Go to task →':'Review closure →';document.querySelectorAll('.stage').forEach(stage=>{const boxes=[...stage.querySelectorAll('.todo input')];const done=boxes.every(x=>x.checked);stage.classList.toggle('done',done);document.querySelector(`[data-stage-link="${stage.id}"]`).classList.toggle('done',done);});save();}
inputs.forEach(input=>{if(typeof state.knobs[input.id]==='string') input.value=state.knobs[input.id];input.addEventListener('input',()=>{state.knobs[input.id]=input.value;updateCommands();save();});});
checks.forEach(input=>{input.checked=state.checked.includes(input.id);input.addEventListener('change',updateProgress);});
function revealHash(){const target=document.getElementById(location.hash.slice(1));if(target?.classList.contains('stage')) target.open=true;document.querySelectorAll('[data-stage-link]').forEach(a=>a.classList.toggle('active',a.hash===location.hash));}
window.addEventListener('hashchange',revealHash);
document.querySelectorAll('a[href^="#"]').forEach(a=>a.addEventListener('click',()=>{const target=document.getElementById(a.hash.slice(1));if(target?.classList.contains('stage')) target.open=true;}));
document.getElementById('expand-all').addEventListener('click',()=>document.querySelectorAll('.stage').forEach(x=>x.open=true));
document.getElementById('collapse-all').addEventListener('click',()=>document.querySelectorAll('.stage').forEach(x=>x.open=false));
document.getElementById('reset-progress').addEventListener('click',()=>{if(confirm('Reset only this page’s checklist marks? Setup values and all actual experiment files remain untouched.')){checks.forEach(x=>x.checked=false);updateProgress();}});
document.querySelectorAll('.copy').forEach(button=>button.addEventListener('click',async()=>{const code=document.getElementById(button.dataset.command);const text=code.textContent;try{if(navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);else throw new Error('fallback');button.textContent='Copied';setTimeout(()=>button.textContent='Copy',1800);notify('Command text copied. Run it yourself in the labelled terminal.');}catch{const selection=window.getSelection();selection.removeAllRanges();const range=document.createRange();range.selectNodeContents(code);selection.addRange(range);notify('Command selected. Press ⌘C / Ctrl+C to copy.');}}));
window.addEventListener('beforeprint',()=>{document.querySelectorAll('.stage').forEach(x=>{x.dataset.printOpen=String(x.open);x.open=true;});});
window.addEventListener('afterprint',()=>document.querySelectorAll('.stage').forEach(x=>x.open=x.dataset.printOpen==='true'));
updateCommands();updateProgress();revealHash();
'''


def knob(id_, label, placeholder, *, token='', type_='text', help_=''):
    pattern = r'[A-Za-z0-9][A-Za-z0-9._\-]*' if type_ == 'text' else ''
    if id_ == 'ssh-user': pattern = r'[A-Za-z_][A-Za-z0-9_\-]*'
    attrs = f' pattern="{pattern}"' if pattern else ''
    return f'<label class="field" for="{id_}">{label}<input id="{id_}" data-knob data-token="{token}" type="{type_}" placeholder="{placeholder}" autocomplete="off" spellcheck="false"{attrs}>' + (f'<small>{help_}</small>' if help_ else '') + '</label>'


def build(source: str) -> str:
    chunks = re.split(r'^## \d+\. .*\n', source, flags=re.M)
    if len(chunks) != 8: raise ValueError('Expected seven Markdown sections; review the HTML stage mapping.')
    acceptance, rest = chunks[4].split('**Review acceptance/native builds/renewal/cleanup with Codex**', 1)
    calibration, day = ('**Review acceptance/native builds/renewal/cleanup with Codex**'+rest).split('**Ask Codex to review/freeze common N and storage/export evidence**',1)
    parts = [chunks[1], chunks[2], chunks[3], acceptance, calibration, '**Ask Codex to review/freeze common N and storage/export evidence**'+day, chunks[5], chunks[6], chunks[7]]
    counter = [0]; cards=[]; nav=[]
    for index, ((id_, title, kind, intro, tasks), body) in enumerate(zip(STAGES, parts), 1):
        nav.append(f'<li><a href="#{id_}" data-stage-link="{id_}"><span class="number">{index:02}</span><span>{title}</span></a></li>')
        todo=''.join(f'<label class="todo" for="{id_}-todo-{j}"><input type="checkbox" id="{id_}-todo-{j}" data-label="{html.escape(task, quote=True)}"><span>{inline(task)}</span></label>' for j,task in enumerate(tasks))
        cards.append(f'<details class="stage" id="{id_}"'+(' open' if index==1 else '')+f'><summary><span class="step-index">{index:02}</span><div class="stage-title"><span class="stage-kind">{kind}</span><h3>{title}</h3><p>{intro}</p></div><span class="stage-caret" aria-hidden="true">›</span></summary><div class="stage-body">'+render_markdown(body,counter)+f'<div class="checklist"><h4>Done for this stage when</h4>{todo}</div></div></details>')
    if counter[0] != len(COMMAND_LABELS): raise ValueError('Command blocks changed; review terminal labels.')
    fields=''.join([knob('ssh-user','SSH user','YOUR_USER',token='YOUR_USER'),knob('ssh-host','Runner hostname / IPv4','YOUR_RUNNER',token='YOUR_RUNNER',help_='Use an SSH alias for custom ports.'),knob('aws-profile','AWS profile','cnb',token='AWS_PROFILE=cnb',help_='Profile configured on the runner.'),knob('azure-app','Azure application ID','ACTUAL_APP_ID',token='ACTUAL_APP_ID'),knob('azure-tenant','Azure tenant ID','ACTUAL_TENANT_ID',token='ACTUAL_TENANT_ID'),knob('azure-subscription','Azure subscription ID','ACTUAL_SUBSCRIPTION_ID',token='ACTUAL_SUBSCRIPTION_ID'),knob('gcp-project','GCP project ID','ACTUAL_PROJECT_ID',token='ACTUAL_PROJECT_ID'),knob('stage-id','Current stage ID','ACTUAL_STAGE_ID',token='ACTUAL_STAGE_ID',help_='Copy from campaign status.'),knob('entry-id','Closed entry ID','ACTUAL_ENTRY_ID',token='ACTUAL_ENTRY_ID',help_='Copy an entry with verified export.')])
    # AWS is a whole assignment replacement, so keep its shell variable prefix.
    script=JS.replace('value=value.split(token).join(replacement);','value=value.split(token).join(token===\'AWS_PROFILE=cnb\'?\'AWS_PROFILE=\'+replacement:replacement);')
    study=''.join([knob('week-date','First WEEK day (UTC)','',type_='date'),knob('night','Night window (UTC)','',type_='time'),knob('morning','Morning window (UTC)','',type_='time'),knob('afternoon','Afternoon window (UTC)','',type_='time'),knob('evening','Evening window (UTC)','',type_='time')])
    digest=hashlib.sha256(source.encode()).hexdigest()
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="light"><title>Campaign field guide · Cloud Network Benchmark</title><style>{CSS}</style></head>
<body><a class="skip" href="#main">Skip to the guide</a><div class="shell"><aside class="sidebar"><div class="brand">Cloud Network Benchmark</div><h2>Your experiment<br>field guide.</h2><div id="progress-label" class="small" aria-live="polite">0 tasks marked</div><div class="progress-track"><div class="progress-fill" id="progress-fill"></div></div><ol class="route">{''.join(nav)}</ol><div class="side-note">Checklist marks are personal notes, not measured status or launch authorization.<a href="campaign-runner.md">Markdown source ↗</a><a href="campaign-developer.md">Evidence & recovery contracts ↗</a><button id="reset-progress" type="button">Reset checklist marks</button></div></aside>
<main id="main"><header class="hero"><div class="eyebrow">Operator runbook · 6 November 2026</div><h1>From setup to<br>a finished study.</h1><p class="lede">Prepare the runner. Prove collection and cleanup. Run the day, then the week — and start writing as verified observations arrive.</p><div class="badges"><span class="badge">Runner collects · laptop analyses</span><span class="badge">Offline-tested driver</span><span class="badge warn">Native / cloud acceptance pending</span></div><div class="boundary"><strong>Keep the gates visible.</strong> $300 total · $250 working · $50 protected. N and UTC windows come from real evidence. This page copies command text; it never connects to a cloud or runs it.</div></header>
<noscript><p>JavaScript is disabled. All steps and command text remain available below; browser checklists, substitutions and copy buttons need JavaScript.</p></noscript>
<div class="next"><div><div class="eyebrow">Next unchecked task</div><p id="next-task">Obtain the runner and real account details.</p></div><a id="next-link" href="#accounts">Go to task →</a></div>
<section class="panel" id="setup"><h2>Your setup values</h2><p>Optional: enter real, non-secret identifiers to personalise the commands. Blank fields keep their placeholders. These fields do not edit operator.yaml.</p><div class="fields">{fields}</div><p class="control-note" id="input-note"></p><details class="field-group"><summary>Record the study choices after calibration and DAY</summary><p>Personal notes only. After review, put the actual values and evidence into the private operator YAML. These notes do not select N, schedule a study or supply an approval.</p><div class="fields"><label class="field" for="common-n">Reviewed common N<select id="common-n" data-knob><option value="">Await calibration review</option><option value="2">2</option><option value="4">4</option><option value="8">8</option></select></label>{study}</div><div class="table-wrap"><table><thead><tr><th>Knob in operator.yaml</th><th>When to set it</th></tr></thead><tbody><tr><td>minimum_free_bytes / size_export_review</td><td>After measured file growth and verified guest/controller/export fit. 200 GB controller storage is provisional.</td></tr><tr><td>common_n_recommendation</td><td>After local calibration analysis and Codex review.</td></tr><tr><td>start_utc / allowances</td><td>Future calibration/DAY start and reviewed whole-workflow timing.</td></tr><tr><td>first_day_utc / windows_utc</td><td>After DAY coverage, budget and seven-day calendar review.</td></tr><tr><td>backup_destination / notification_hook</td><td>Optional; configure and test actual off-machine copying / delivery.</td></tr></tbody></table></div></details><p class="control-note" id="save-note">Stored only in your browser. Never enter keys or tokens.</p></section>
<div class="toolbar"><h2>The path to finish</h2><div><button id="expand-all" type="button">Expand all</button><button id="collapse-all" type="button">Collapse all</button></div></div>
{''.join(cards)}
<section class="panel"><h2>What “finished” means</h2><div class="finish-map"><div><strong>01 · Evidence closed</strong><span>Final raw copies verified. Expected, failed and missed observations reconciled. Private recovery state retained.</span></div><div><strong>02 · Resources closed</strong><span>All experiment VMs, disks, addresses and support resources verified absent. Costs reconciled.</span></div><div><strong>03 · Writing grounded</strong><span>Final reports, source IDs and analysis revision linked to Thesis findings. Interim claims replaced by final evidence.</span></div></div></section>
<footer>Generated from <a href="campaign-runner.md">campaign-runner.md</a>. Source SHA256: {digest}<br>Refresh after Markdown changes: <code>python3 scripts/render_campaign_runbook.py</code> · verify: <code>python3 scripts/render_campaign_runbook.py --check</code>. All command blocks preserve the Markdown source until you supply setup values. No external fonts, scripts, network requests or execution controls.</footer></main></div><div class="toast" id="toast" role="status" aria-live="polite"></div><script>{script}</script></body></html>\n'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    root=Path(__file__).resolve().parents[1]
    parser.add_argument('--source',type=Path,default=root/'docs/campaign-runner.md')
    parser.add_argument('--output',type=Path,default=root/'docs/campaign-runner.html')
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    rendered=build(args.source.read_text(encoding='utf-8'))
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding='utf-8') != rendered:
            parser.exit(1,'HTML is stale; run python3 scripts/render_campaign_runbook.py\n')
        print('HTML matches the Markdown source and presentation template.')
    else:
        args.output.write_text(rendered,encoding='utf-8')
        print(args.output)

if __name__=='__main__':main()
