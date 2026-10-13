"""An actual synthetic stdio transport and local HTTP request; no real Sentry account."""
import ast
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

import test_sentry_plugin as fixture
import dashboard


def assert_no_credentials(test, folder):
    """Scan data/docs and Python literals; ordinary source assignments are not credentials."""
    for path in folder.rglob('*'):
        if not path.is_file() or path.suffix in ('.sqlite3','.pyc'):
            continue
        try: text=path.read_text()
        except UnicodeError:continue
        strong=fixture.bridge.SENSITIVE.pattern.split(r'|\b(?:')[0]
        test.assertIsNone(re.search(strong,text),str(path))
        if path.suffix=='.py':
            for node in ast.walk(ast.parse(text)):
                if isinstance(node,ast.Constant) and isinstance(node.value,str):
                    test.assertIsNone(fixture.bridge.SENSITIVE.search(node.value),str(path))
        else:
            test.assertIsNone(fixture.bridge.SENSITIVE.search(text),str(path))


class SentryJourney(fixture.SentryFixture, unittest.TestCase):
    def test_credential_scan_detects_planted_patterns_in_docs_data_and_python_literals(self):
        folder=self.folder/'scan';folder.mkdir()
        for suffix in ('.md','.json','.py'):
            path=folder/('fixture'+suffix)
            for value in ('sntry'+'u_SYNTHETIC_ONLY','https://synthetic@sentry.example.invalid/1',
                          'Bearer SYNTHETIC_ONLY','api_key=SYNTHETIC_ONLY'):
                with self.subTest(suffix=suffix,shape=value.split(':')[0]):
                    path.write_text('value='+repr(value) if suffix=='.py' else json.dumps({'text':value}))
                    with self.assertRaises(AssertionError):assert_no_credentials(self,folder)
            path.unlink()

    def command(self, argv, cwd=None):
        result=subprocess.run(argv,cwd=cwd or self.folder,env={**os.environ,'HOME':str(self.home)},
                              capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return result

    def squad(self,*args):
        return self.command([sys.executable,'-B',str(fixture.ROOT/'scripts/squadra.py'),*args,'--root',str(self.root)])

    def test_synthetic_mcp_to_http_selection_coordinator_task_and_proof(self):
        requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},
                  {'jsonrpc':'2.0','method':'notifications/initialized'},
                  {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                  {'jsonrpc':'2.0','id':3,'method':'tools/call',
                   'params':{'name':'synthetic_issue_summaries','arguments':{}}}]
        run=subprocess.run([sys.executable,'-I','-B',str(fixture.ROOT/'tests/sentry_synthetic_mcp.py')],
                           input=''.join(json.dumps(x)+'\n' for x in requests),capture_output=True,text=True,
                           cwd=self.folder,env={**os.environ,'HOME':str(self.home)},timeout=10)
        self.assertEqual(run.returncode,0,run.stderr)
        replies=[json.loads(x) for x in run.stdout.splitlines()]
        self.assertEqual([x['id'] for x in replies],[1,2,3])
        self.assertEqual(replies[0]['result']['serverInfo']['name'],'synthetic-only-fixture')
        self.assertEqual(replies[1]['result']['tools'][0]['name'],'synthetic_issue_summaries')
        self.assertFalse(replies[2]['result']['isError'])
        observed=json.loads(replies[2]['result']['content'][0]['text'])
        # The agent selects only the local schema after inspecting discovered tools.
        self.assertEqual(set(observed),{'schema','organization','project','observed','availability','issues'})
        source=self.write('sanitised.json',observed)
        imported=self.cli(['import','--organization','example-org','--project','example-app','--file',str(source)])
        identity=imported['ids'][0]
        with patch.dict(os.environ,HOME=str(self.home)):
            server,session=dashboard.make_server(self.root)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request_http(method,path,body=None):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            try:
                connection.request(method,path,None if body is None else json.dumps(body),
                                   {'Host':'127.0.0.1:'+str(server.server_port),'X-Squadra-Token':session,
                                    'Content-Type':'application/json'})
                reply=connection.getresponse();data=json.loads(reply.read())
                self.assertEqual(reply.status,200,data);return data
            finally:connection.close()
        try:
            state=request_http('GET','/api/state');self.assertEqual(state['plugins']['key'],self.key)
            request_http('POST','/api/plugin/sentry/action/analyse',{'ids':[identity]})
            pending=self.cli(['pending']);snapshot=self.write('pending.json',pending)
            self.assertEqual(pending['request']['ids'],[identity])
            repo=self.folder/'work';repo.mkdir();(repo/'probe.txt').write_text('Synthetic diagnostic fixture\n')
            self.command(['git','init','-q',str(repo)])
            self.command(['git','add','probe.txt'],repo)
            self.command(['git','-c','user.name=Synthetic','-c','user.email=fixture@example.invalid','commit','-qm','Synthetic fixture'],repo)
            scope=self.folder/'scope.md';scope.write_text('Analyse the synthetic checkout observation; no live provider.')
            proof=[sys.executable,'-c','print("Synthetic diagnostic; not a Sentry execution")']
            proof_file=self.write('proof.json',proof)
            self.squad('task','--actor','C','--id','ANALYSIS','--owner','A','--title','Synthetic observed checkout analysis',
                       '--repo',str(repo),'--scope-file',str(scope),'--proof-file',str(proof_file))
            self.squad('ack','--actor','A','--message','assign-ANALYSIS')
            self.squad('start','--actor','A','--task','ANALYSIS')
            self.command([sys.executable,'-B',str(fixture.ROOT/'scripts/squadra.py'),'prove','--actor','A','--task','ANALYSIS',
                          '--root',str(self.root),'--timeout','15','--',*proof])
            runs=self.store.state()['runs'];run_id=next(k for k,v in runs.items() if v['task']=='ANALYSIS')
            self.assertEqual(runs[run_id]['status'],'passed')
            before=self.store.events()
            result=self.write('result.json',{'schema':1,'results':[self.result(identity,task='ANALYSIS',run=run_id)]})
            recorded=self.cli(['record','--snapshot',str(snapshot),'--file',str(result)])
            self.assertTrue(recorded['consumed']);self.assertEqual(self.store.events(),before)
            final=request_http('GET','/api/plugin/sentry')['panel']['items'][0]
            self.assertEqual(final['refs'],[{'task':'ANALYSIS','run':run_id}]);self.assertEqual(final['status'],'sent')
            self.assertIn('unresolved',[x['value'] for x in final['meta']])
            transcript={'kind':'synthetic MCP transport only; not Sentry tool names or real OAuth',
                        'requests':requests,'replies':replies,'imported':imported,
                        'selected':{'ids':[identity]},'recorded':recorded,'panel':final,
                        'real_sentry_oauth':'pending'}
            transcript_folder=self.folder/'transcript';transcript_folder.mkdir()
            (transcript_folder/'journey.json').write_text(json.dumps(transcript,indent=2))
            assert_no_credentials(self,fixture.ROOT/'plugins/sentry')
            assert_no_credentials(self,self.plugin)
            assert_no_credentials(self,transcript_folder)
            destination=os.environ.get('SQUADRA_SENTRY_JOURNEY_EVIDENCE')
            if destination:
                target=Path(destination);target.mkdir(parents=True,exist_ok=True)
                (target/'journey.json').write_text(json.dumps(transcript,indent=2))
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5);self.assertFalse(thread.is_alive())
