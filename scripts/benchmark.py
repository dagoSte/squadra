"""Run registered client comparisons and score their saved results."""
from __future__ import annotations

import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from registry import Store, Error, canonical, digest, git, now, require, snapshot
from runtime import CURSOR_REFUSAL, cli_command, private_temp, remove_temp, temp_parent, terminate, validate_reply


def check_pause(store, message):
    if store is None:
        return
    try:
        require(not store.view()['paused'], message)
    finally:
        store.release_views()


OFFLINE_CLIENT = '''#!/usr/bin/env python3
import json,sys,uuid
from pathlib import Path
if '--version' in sys.argv:print('squadra offline fake 1');sys.exit(0)
p=json.loads(sys.stdin.read());session=str(uuid.uuid4());case=p['case']
if p['kind']=='fix':
 Path('code.py').write_text('VALUE = 2\\n');reply='Synthetic fix, code.py only'
else:
 findings=['D1 identity code.py:1','D2 guard code.py:2','D3 claim code.py:5'] if case=='K2' else ['D4 count code.py:2'] if case=='K3' else []
 reply={'verdict':'changes-required' if findings else 'accepted','summary':'Synthetic result','findings':findings,'coverage':['fixture source'],'limitations':['Fake client; no model measurement']}
if '-o' in sys.argv:
 Path(sys.argv[sys.argv.index('-o')+1]).write_text(json.dumps(reply) if isinstance(reply,dict) else reply)
 print(json.dumps({'type':'thread.started','thread_id':session}));print(json.dumps({'type':'turn.completed'}))
else:print(json.dumps({'type':'result','subtype':'success','is_error':False,'session_id':session,('structured_output' if isinstance(reply,dict) else 'result'):reply}))
'''

def example(destination):
    root=Path(destination).resolve();require(not root.exists(),'Example destination must be new');root.mkdir(parents=True,mode=0o700)
    cases=[]
    bodies={'K1':'VALUE = 1\n','K2':'def same(a,b): return a.lower()==b.lower()\ndef allowed(roles): return [r for r in roles if r] # a refusal still permits survivors\ndef claimed():\n """Returns two."""\n return 1\nDECOY_EXPLICIT_EMPTY = []\nDECOY_UNUSUAL_VALID = bool(0)\n',
            'K3':'# claimed: all tests, actual command ignores nested tests and masks exit\nCOMMAND = "python3 tests/test_root.py | tail -1"\n', 'K4':'def same(a,b): return a==b\n'}
    for case,body in bodies.items():
        repo=root/case;repo.mkdir();(repo/'code.py').write_text(body)
        if case=='K3':
            (repo/'tests/nested').mkdir(parents=True)
            (repo/'tests/nested/__init__.py').write_text('')
            (repo/'tests/test_root.py').write_text('import unittest\nclass Root(unittest.TestCase):\n def test_planted_failure(self): self.fail("planted failure")\nif __name__=="__main__":unittest.main()\n')
            (repo/'tests/nested/test_other.py').write_text('import unittest\nclass Nested(unittest.TestCase):\n def test_other(self): self.assertTrue(True)\n')
            git(repo,'init','-q');git(repo,'add','tests')
        git(repo,'init','-q');git(repo,'add','code.py');git(repo,'-c','user.name=Squadra synthetic','-c','user.email=synthetic@example.invalid','-c','commit.gpgsign=false','commit','-qm','Synthetic benchmark fixture')
        cases.append({'id':case,'fixture':str(repo),'head':git(repo,'rev-parse','HEAD').decode().strip(),'kind':'fix' if case=='K1' else 'read','synthetic':True})
    fake=root/'fake-client.py'
    fake.write_text(OFFLINE_CLIENT);fake.chmod(0o755)
    oracle={'K1':{'assertion':'assert "VALUE = 2" in (repo/"code.py").read_text()'},
            'K2':{'defects':[{'id':'D1','file':'code.py','line':1},{'id':'D2','file':'code.py','line':2},{'id':'D3','file':'code.py','line':5}],
                  'decoys':['DECOY_EXPLICIT_EMPTY','DECOY_UNUSUAL_VALID']},
            'K3':{'defects':[{'id':'D4','file':'code.py','line':2}], 'decoys':[]},'K4':{'defects':[],'decoys':[]}}
    (root/'oracle.json').write_text(canonical(oracle)+'\n')
    arms=[{'id':name,'reader':reader,'author':author,'cli':str(fake),'model':'synthetic','author_provenance':'hand-seeded fixture, not a model author'}
          for name,reader,author in [('A','claude','codex'),('B','codex','claude'),('C','codex','codex'),('D','claude','claude')]]
    manifest={'schema':1,'mode':'offline','question':'Does a different client catch more planted defects at equal false alarms? This example tests the harness only.',
              'oracle_sha256':digest((root/'oracle.json').read_bytes()),'repeats':1,'timeout':30,'arms':arms,'cases':cases,
              'oracle_isolation':'Offline example on same filesystem; contamination unverified','interventions':[]}
    (root/'manifest.json').write_text(canonical(manifest)+'\n')
    return {'manifest':str(root/'manifest.json'),'oracle':str(root/'oracle.json'),'mode':'offline, fake clients only'}


def run(manifest_path,destination,allow_models=False,budget_file=None,store=None):
    manifest_path=Path(manifest_path).resolve(strict=True);body=manifest_path.read_bytes();m=json.loads(body)
    require(m.get('schema')==1 and m.get('mode') in {'offline','models'},'Unsupported benchmark manifest')
    require(type(m.get('repeats')) is int and 1<=m['repeats']<=10 and type(m.get('timeout')) is int and 1<=m['timeout']<=3600,'Bounded repetitions/timeout required')
    require(1<=len(m['arms'])<=4 and 1<=len(m['cases'])<=4,'Bounded arms/cases required')
    require(len({a['id'] for a in m['arms']})==len(m['arms']) and len({c['id'] for c in m['cases']})==len(m['cases']),'Unique arm/case IDs required')
    for item in [*m['arms'],*m['cases']]:require(re.fullmatch(r'[A-Za-z0-9_-]+',item['id']), 'Safe arm/case ID required')
    require(re.fullmatch(r'[a-f0-9]{64}',m['oracle_sha256']),'Pre-registered oracle hash required')
    for a in m['arms']:
        require(a.get('reader')!='cursor',CURSOR_REFUSAL)
        require(a['reader'] in {'codex','claude'} and a.get('author_provenance'),'Declared reader and author provenance required')
        if m['mode']=='offline':
            require(isinstance(a.get('cli'),str) and a['cli'].strip(), 'Offline requires an explicit bundled fake CLI')
            fake=Path(a['cli']).resolve(strict=True)
            require(fake.is_file() and digest(fake.read_bytes())==digest(OFFLINE_CLIENT.encode()),
                    'Offline accepts only the bundled synthetic client; custom clients need explicit models budget')
            a['cli']=str(fake)
    for c in m['cases']:
        require(c.get('synthetic') is True and c['kind'] in {'fix','read'},'Synthetic fixtures and known kinds required')
        repo=Path(c['fixture']).resolve(strict=True);require(git(repo,'rev-parse','HEAD').decode().strip()==c['head'] and not git(repo,'status','--porcelain').strip(),'Frozen fixture drift')
    if m['mode']=='models':
        require(allow_models and budget_file,'Real turns require --allow-models and an explicit owner budget file')
        budget=json.loads(Path(budget_file).read_text());require(budget.get('owner_words') and type(budget.get('max_turns')) is int and budget['max_turns']>=len(m['arms'])*len(m['cases'])*m['repeats'],'Insufficient owner-scoped turn budget')
    folder=Path(destination).resolve();require(not folder.exists(),'Benchmark output must be new')
    parent=temp_parent({'the benchmark output':folder}) if m['mode']=='offline' else None
    require(not any(Path(c['fixture']).resolve() in folder.parents for c in m['cases']),'Output outside fixtures')
    folder.mkdir(parents=True,mode=0o700);(folder/'manifest.json').write_bytes(body)
    result={'schema':1,'status':'running','manifest_sha256':digest(body),'oracle_sha256':m['oracle_sha256'],'mode':m['mode'],'started':now(),
            'machine':os.uname().sysname+' '+os.uname().machine,'turns':[],'retries':0,'interventions':m.get('interventions',[]),
            'limitations':['No quality/superiority conclusion from an offline example.','Native wrapper is not a filesystem secret vault.','Trace contamination is not fully observable; author labels need real provenance.','Codex and Claude default read permissions differ; report separate cohorts.']}
    sessions=set();row=None
    try:
        for a in m['arms']:
            for c in m['cases']:
                for repeat in range(m['repeats']):
                    check_pause(store, 'Benchmark stopped by owner pause')
                    turn=folder/(a['id']+'-'+c['id']+'-'+str(repeat));turn.mkdir(mode=0o700)
                    repo=turn/'fixture';git(folder,'clone','--no-hardlinks',c['fixture'],str(repo));git(repo,'checkout','--detach',c['head'])
                    # Separate registries keep each run from seeing another comparison arm's observations.
                    record=Store(turn/'registry',create=True)
                    record.append('init','system',{'name':'Benchmark '+turn.name,'project':str(repo),'limits':{'plan':1,'code':1,'build':1}});record.close()
                    prompt=canonical({'case':c['id'],'kind':c['kind'],'instruction':'Inspect or fix the synthetic fixture only. Do not commit/publish. Return actual coverage and limitations.'})
                    (turn/'input.txt').write_text(prompt);from runtime import REVIEW_SCHEMA
                    (turn/'schema.json').write_text(json.dumps(REVIEW_SCHEMA))
                    argv=cli_command(a['reader'],'build' if c['kind']=='fix' else 'read',turn,a.get('model'),a.get('cli'))
                    before=snapshot(repo);started=time.monotonic();row={'arm':a['id'],'case':c['id'],'kind':c['kind'],'author':a['author'],'author_provenance':a['author_provenance'],
                        'reader':a['reader'],'requested_model':a.get('model'),'permissions':'workspace-write' if c['kind']=='fix' else 'codex read-only shell' if a['reader']=='codex' else 'claude Read,Glob,Grep',
                        'argv':argv,'prompt_sha256':digest(prompt.encode()),'fixture_head':c['head'],'before':before,'folder':str(turn),'status':'failed','native_rc':None,'usage':None,'observability':'unknown','utc':now()}
                    result['turns'].append(row)
                    version=subprocess.run([argv[0],'--version'],capture_output=True,text=True,timeout=15);require(version.returncode==0,'CLI preflight failed');row['cli_version']=version.stdout.strip()
                    env=os.environ|{'PYTHONDONTWRITEBYTECODE':'1'}
                    temp=None
                    if m['mode']=='offline':
                        temp=row['temp']=private_temp(parent);env.update(HOME=temp,TMPDIR=temp)
                    try:
                        with (turn/'stdout.log').open('wb') as out,(turn/'stderr.log').open('wb') as err:
                            proc=subprocess.Popen(argv,cwd=repo,env=env,stdin=subprocess.PIPE,stdout=out,stderr=err,start_new_session=True)
                            try:
                                proc.stdin.write(prompt.encode());proc.stdin.close()
                                while proc.poll() is None:
                                    check_pause(store, 'Owner pause during benchmark')
                                    require(time.monotonic()-started<=m['timeout'],'Benchmark timeout')
                                    time.sleep(0.05)
                                row['native_rc']=proc.wait();require(row['native_rc']==0,'Native turn failed')
                            finally:
                                try:terminate(proc);row['cleanup']='owned group disposed'
                                except Exception as exc:
                                    row.update(status='cleanup-unknown',cleanup='not verified',cleanup_error=str(exc))
                                    raise
                                finally:row['native_rc']=proc.poll()
                    finally:
                        if temp:row.update(remove_temp(temp))
                    row.update(validate_reply(a['reader'],turn,structured=c['kind']=='read'));require(row['actual_session'] not in sessions,'Reused session, fresh reader not established');sessions.add(row['actual_session'])
                    after=snapshot(repo);require(after==before if c['kind']=='read' else after['head']==before['head'],'Unexpected source drift/commit')
                    row.update(after=after,status='completed',seconds=round(time.monotonic()-started,3),
                               artifacts={p.name:digest(p.read_bytes()) for p in turn.iterdir() if p.is_file() and p.name in {'stdout.log','stderr.log','input.txt','schema.json','reply.txt'}})
        result['status']='completed';return result
    except (Exception,KeyboardInterrupt) as exc:
        result.update(status='failed',error=str(exc) or 'Owner interruption')
        if row is not None and row['status']!='completed':
            row.update(error=result['error'],seconds=round(time.monotonic()-started,3))
        return result
    finally:
        result['ended']=now();(folder/'RUNS.json').write_text(canonical(result)+'\n')


def score(run_folder,oracle_path):
    folder=Path(run_folder).resolve(strict=True);result=json.loads((folder/'RUNS.json').read_text())
    body=Path(oracle_path).read_bytes();require(digest(body)==result['oracle_sha256'],'Oracle differs from pre-registered hash')
    oracle=json.loads(body);scored=[]
    require(digest((folder/'manifest.json').read_bytes())==result['manifest_sha256'],'Registered manifest changed')
    require(result['status']=='completed' and all(t['status']=='completed' for t in result['turns']),'Incomplete native turns cannot be scored as complete')
    for turn in result['turns']:
        path=Path(turn['folder']).resolve();require(path.parent==folder,'Turn path escapes benchmark output')
        for name,sha in turn['artifacts'].items():
            require(Path(name).name==name and digest((path/name).read_bytes())==sha,'Native turn artifact drift')
        reply=validate_reply(turn['reader'],path,structured=turn['kind']=='read')
        require(all(reply[k]==turn[k] for k in reply),'Native response/usage/session differs from saved turn')
        definition=oracle[turn['case']];row={'arm':turn['arm'],'case':turn['case'],'reader':turn['reader'],'permissions':turn['permissions'],'seconds':turn['seconds'],'usage':turn['usage']}
        if turn['kind']=='fix':
            repo=Path(turn['folder'])/'fixture';require(snapshot(repo)==turn['after'],'Post-turn candidate drift before scoring')
            probe=Path(turn['folder'])/'hidden-check.py'
            import ast
            require(isinstance(definition.get('assertion'),str) and any(isinstance(n,ast.Assert) for n in ast.walk(ast.parse(definition['assertion']))),'Hidden grading check must contain an assertion')
            probe.write_text('from pathlib import Path\nrepo=Path.cwd()\n'+definition['assertion']+'\n')
            p=subprocess.run([sys.executable,'-B',str(probe)],cwd=repo,capture_output=True,timeout=10)
            (Path(turn['folder'])/'hidden.stdout.log').write_bytes(p.stdout);(Path(turn['folder'])/'hidden.stderr.log').write_bytes(p.stderr)
            row['hidden_native_rc']=p.returncode;row['fixed']=p.returncode==0
        else:
            row.update(grade_findings(turn['response'],definition))
        scored.append(row)
    report={'mode':result['mode'],'status':'scored','turns':scored,'comparative_inference':'Not established; inspect provenance, equal capabilities, repeated samples and contamination before conclusions.',
            'limitations':result['limitations'],'oracle_sha256':result['oracle_sha256']}
    (folder/'SCORE.json').write_text(canonical(report)+'\n')
    (folder/'REPORT.html').write_text('<!doctype html><meta charset="utf-8"><title>Squadra benchmark</title><h1>Observed benchmark evidence</h1><p>'+html.escape(report['comparative_inference'])+'</p><pre>'+html.escape(json.dumps(report,ensure_ascii=False,indent=2))+'</pre>')
    return report


def grade_findings(response,definition):
    findings=response['findings'];found=set();false=set();ambiguous=[]
    for finding in findings:
        matches=[d['id'] for d in definition.get('defects',[]) if re.search(r'(?<![\w-])'+re.escape(d['id'])+r'(?![\w-])',finding)]
        if not matches:
            matches=[d['id'] for d in definition.get('defects',[]) if any(abs(int(n)-d['line'])<=3 for n in re.findall(re.escape(d['file'])+r':(\d+)',finding))]
        decoys=[d for d in definition.get('decoys',[]) if d in finding]
        if len(matches)==1 and not decoys:found.update(matches)
        elif decoys:false.update(decoys)
        else:ambiguous.append(finding)
    row={'found':sorted(found),'expected':len(definition.get('defects',[])),'false_alarms':sorted(false),'ambiguous':ambiguous,'verdict':response['verdict']}
    if not definition.get('defects'):
        row.update(clean_control_pass=response['verdict']=='accepted' and not findings,clean_false_alarms=len(findings))
    return row
