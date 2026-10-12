"""Check offline client comparisons, scoring and run limits."""
import json
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))

from benchmark import example,run,score,grade_findings
from registry import Error,Store
from squadra import main
import contextlib,io


class Benchmark(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.paths=example(self.base/'example')
        self.manifest=Path(self.paths['manifest']);self.oracle=Path(self.paths['oracle'])

    def test_all_four_arms_native_fix_read_and_clean_control(self):
        result=run(self.manifest,self.base/'runs')
        self.assertEqual(result['status'],'completed');self.assertEqual(len(result['turns']),16)
        self.assertEqual(len({x['actual_session'] for x in result['turns']}),16)
        self.assertTrue(all(x['native_rc']==0 and x['usage'] is None for x in result['turns']))
        report=score(self.base/'runs',self.oracle)
        for row in report['turns']:
            if row['case']=='K1':self.assertTrue(row['fixed']);self.assertEqual(row['hidden_native_rc'],0)
            elif row['case']=='K2':self.assertEqual(row['found'],['D1','D2','D3']);self.assertFalse(row['false_alarms'])
            elif row['case']=='K3':self.assertEqual(row['found'],['D4'])
            else:self.assertTrue(row['clean_control_pass'])
        self.assertIn('Not established',report['comparative_inference'])
        self.assertTrue((self.base/'runs/REPORT.html').is_file())

    def test_models_need_explicit_owner_budget_before_any_turn(self):
        value=json.loads(self.manifest.read_text());value['mode']='models';self.manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(Error,'owner budget'):run(self.manifest,self.base/'runs')
        self.assertFalse((self.base/'runs').exists())

    def test_oracle_hash_mismatch_and_input_drift_refused(self):
        value=json.loads(self.manifest.read_text());value['arms']=value['arms'][:1];value['cases']=value['cases'][-1:];self.manifest.write_text(json.dumps(value))
        run(self.manifest,self.base/'runs');self.oracle.write_text('tampered')
        with self.assertRaisesRegex(Error,'pre-registered'):score(self.base/'runs',self.oracle)
        (Path(value['cases'][0]['fixture'])/'code.py').write_text('unfrozen')
        with self.assertRaisesRegex(Error,'drift'):run(self.manifest,self.base/'drift')

    def test_decoys_ambiguous_findings_and_clean_false_alarms_count_separately(self):
        value=json.loads(self.manifest.read_text());value['arms']=value['arms'][:1];value['cases']=value['cases'][1:];self.manifest.write_text(json.dumps(value))
        result=run(self.manifest,self.base/'runs')
        definitions=json.loads(self.oracle.read_text())
        k2=grade_findings({'verdict':'changes-required','findings':['DECOY_EXPLICIT_EMPTY: wrong alarm','unknown finding']},definitions['K2'])
        self.assertEqual(k2['false_alarms'],['DECOY_EXPLICIT_EMPTY']);self.assertEqual(len(k2['ambiguous']),1)
        k4=grade_findings({'verdict':'changes-required','findings':['invented clean defect']},definitions['K4'])
        self.assertFalse(k4['clean_control_pass']);self.assertEqual(k4['clean_false_alarms'],1)
        result['turns'][0]['response']['findings']=['forged native response']
        (self.base/'runs/RUNS.json').write_text(json.dumps(result))
        with self.assertRaisesRegex(Error,'Native response'):score(self.base/'runs',self.oracle)

    def test_native_failure_keeps_rc_logs_and_registry_receipt(self):
        fake=self.base/'example/fake-client.py'
        fake.write_text('#!/usr/bin/env python3\nimport sys\nif "--version" in sys.argv:print("failing fake 1");sys.exit(0)\nsys.exit(7)\n')
        store=Store(self.base/'registry',create=True)
        store.append('init','system',{'name':'Benchmark failures','project':str(self.base),'limits':{'plan':3,'code':2,'build':3}})
        store.append('agent','C',{'id':'C','identity':'coordinator','role':'coordinator','client':'manual','repo':str(self.base)})
        store.close()
        m=json.loads(self.manifest.read_text());m['mode']='models';self.manifest.write_text(json.dumps(m))
        budget=self.base/'synthetic-budget.json';budget.write_text(json.dumps({'owner_words':'Synthetic fake failure control; no actual model','max_turns':16}))
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            rc=main(['--root',str(self.base/'registry'),'bench-run','--actor','C','--manifest',str(self.manifest),'--output',str(self.base/'failed'),'--allow-models','--budget-file',str(budget)])
        self.assertEqual(rc,1)
        result=json.loads((self.base/'failed/RUNS.json').read_text())
        self.assertEqual(result['status'],'failed');self.assertEqual(result['turns'][0]['native_rc'],7)
        self.assertTrue((Path(result['turns'][0]['folder'])/'stderr.log').is_file())
        with self.assertRaisesRegex(Error,'Incomplete'):score(self.base/'failed',self.oracle)
        store=Store(self.base/'registry')
        try:
            self.assertEqual(store.state()['benchmarks'][-1]['status'],'failed')
            self.assertTrue(store.verify_artifacts())
        finally:store.close()

    def test_offline_requires_bundled_fake_not_installed_or_custom_cli(self):
        from unittest.mock import patch
        for cli in [None,'codex',str(self.base/'custom-client')]:
            if cli and cli.endswith('custom-client'):Path(cli).write_text('fake claims do not prove a client is synthetic')
            value=json.loads(self.manifest.read_text());value['arms']=value['arms'][:1]
            if cli is None:value['arms'][0].pop('cli',None)
            else:value['arms'][0]['cli']=cli
            self.manifest.write_text(json.dumps(value))
            with patch('benchmark.subprocess.Popen',side_effect=AssertionError('Client launched')):
                with self.assertRaises((Error,FileNotFoundError)):run(self.manifest,self.base/'forbidden')
            self.assertFalse((self.base/'forbidden').exists())

    def test_invalid_limits_are_not_an_unbounded_loop(self):
        value=json.loads(self.manifest.read_text());value['repeats']=1000;self.manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(Error,'Bounded'):run(self.manifest,self.base/'runs')
