import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from router.worker import Worker
from router.verification import Checks

class WorkerTests(unittest.TestCase):
    def run_worker(self, script, **project):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        fake = root / 'fake.py'
        fake.write_text(script, encoding='utf-8')
        task = dict(id='t1', project_id='p', goal='test', rules_snapshot='BASE', project=dict(path=str(root), rules='PROJECT', context='CONTEXT', timeout=2, idle_timeout=2, **project))
        worker = Worker([sys.executable, str(fake)], task, root/'run', lambda *args: None)
        worker.start()
        self.addCleanup(worker.stop, 'cleanup')
        deadline = time.monotonic()+5
        while time.monotonic()<deadline:
            result=worker.poll()
            if result: return result, root
            time.sleep(.02)
        self.fail('worker did not finish')

    def test_success_and_prompt(self):
        result, root = self.run_worker("import sys,json\np=sys.stdin.read()\nassert 'BASE' in p and 'CONTEXT' in p\nprint(json.dumps({'type':'result','result':'done'}))")
        self.assertEqual(result['status'], 'reported_unverified')
        self.assertIn('done', result['summary'])
        self.assertTrue((root/'run'/'prompt.txt').exists())

    def test_failure_stderr(self):
        result, root=self.run_worker("import sys\nprint('broken',file=sys.stderr)\nsys.exit(4)")
        self.assertEqual(result['status'],'failed')
        self.assertIn('broken',(root/'run'/'stderr.log').read_text())

    def test_timeout(self):
        result, _=self.run_worker('import time\ntime.sleep(30)')
        self.assertEqual(result['status'],'timed_out')

    def test_credentials_removed(self):
        with patch.dict(os.environ, {'MM_BOT_TOKEN':'secret','AI_CREW_TOKEN':'secret','FEISHU_USER_TOKEN':'secret','QODER_TEST_LOGIN':'keep'}):
            result,_=self.run_worker("import os\nassert not any(k in os.environ for k in ['MM_BOT_TOKEN','AI_CREW_TOKEN','FEISHU_USER_TOKEN'])\nassert os.environ['QODER_TEST_LOGIN']=='keep'\nassert os.environ['QODER_AGENT_TEAMS']=='0'")
        self.assertEqual(result['status'],'reported_unverified')


    def test_acceptance_and_escape(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'proof.txt').write_text('verified', encoding='utf-8')
            task={'goal':'```bridge-checks\n'+json.dumps({'files':[{'path':'proof.txt','equals':'verified'}]})+'\n```', 'project':{'path':str(root)}}
            worker=Worker([],task,root,lambda *args:None)
            worker.checks=Checks(task['goal'],root)
            self.assertEqual(worker._verify(),'succeeded')
            task['acceptance']={'files':[{'path':'../escape','exists':False}]}
            with self.assertRaises(ValueError):
                Checks('```bridge-checks\n'+json.dumps(task['acceptance'])+'\n```',root)

    def test_summary_survives_final_event(self):
        result,_=self.run_worker('import json\nprint(json.dumps({"type":"result","result":"kept"}))\nprint(json.dumps({"type":"system","subtype":"done"}))')
        self.assertIn('> kept',result['summary'])
        self.assertNotIn('subtype',result['summary'])
        self.assertIn('\u672a\u6838\u9a8c',result['summary'])

    def test_invalid_checks(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            for data in ([], {'files':[]}, {'files':[{'path':'x','unknown':True}]}, {'files':[{'path':'C:/escape','exists':True}]}):
                with self.assertRaises(ValueError):
                    Checks('```bridge-checks\n'+json.dumps(data)+'\n```',folder)

    def test_failed_check_report(self):
        from router.verification import Evidence, render_report
        with tempfile.TemporaryDirectory() as folder:
            checks=Checks('```bridge-checks\n{"files":[{"path":"missing.txt","exists":true}]}\n```',folder)
            report, failed=render_report('All done!',False,checks,Evidence())
            self.assertTrue(failed)
            self.assertIn('missing.txt',report)
            self.assertIn('> All done!',report)

    def test_native_async_never_success(self):
        result,_=self.run_worker('import json\nprint(json.dumps({"type":"system","subtype":"task_started"}))\nprint(json.dumps({"type":"result","result":"Waiting for developer"}))')
        self.assertEqual(result['status'],'failed')
        self.assertIn('INCOMPLETE',result['summary'])

    def test_agent_tool_never_success(self):
        result,_=self.run_worker('import json\nprint(json.dumps({"type":"assistant","message":{"content":[{"type":"tool_use","name":"Agent"}]}}))')
        self.assertEqual(result['status'],'failed')

    def test_supervisor_reports_not_parsed_as_acceptance(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            script=root/'fake.py'
            script.write_text('print("review complete")',encoding='utf-8')
            for supervisor in (True,False):
                task={'id':'phase','project_id':'p','goal':'PRIOR REPORT: ```bridge-checks broken nested quoted text','project':{'path':str(root)},'supervisor_phase':supervisor}
                worker=Worker([sys.executable,str(script)],task,root/str(supervisor),lambda *args:None)
                worker.start()
                try:
                    deadline=time.monotonic()+5
                    while worker.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                    self.assertIsNotNone(worker.result)
                    self.assertEqual(worker.result['status'],'reported_unverified' if supervisor else 'failed')
                    if not supervisor:self.assertIsNone(worker.process)
                finally:worker.stop('test cleanup')

    def test_only_exact_preexecution_validation_recoverable(self):
        from router.verification import Evidence
        evidence=Evidence()
        evidence.observe({'type':'assistant','message':{'content':[{'type':'tool_use','id':'w','name':'Write','input':{'file_path':'x'}}]}})
        message="Error: Write tool parameter validation failed: params must have required property 'content'"
        def observe(content,call='w'):
            evidence.observe({'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':call,'is_error':True,'content':content}]}})
        observe(message)
        self.assertEqual(evidence.validation_errors,1)
        self.assertEqual(evidence.tool_errors,0)
        observe('Error: access denied')
        observe('Error: Write tool parameter validation failed: runtime failure')
        observe(message,'unknown')
        self.assertEqual(evidence.tool_errors,3)
        evidence.observe({'type':'result','permission_denials':['denied']})
        self.assertEqual(evidence.permission_denials,1)

    def test_project_tool_path_is_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bindir=root/'bin';bindir.mkdir();fake=root/'probe.py'
            fake.write_text("import os,json;print(json.dumps({'type':'result','result':os.environ.get('PATH','')}))")
            task=dict(id='path',project_id='p',goal='check',project=dict(path=str(root),tool_paths=[str(bindir)],timeout=3,idle_timeout=3))
            before=os.environ.get('PATH')
            w=Worker([sys.executable,str(fake)],task,root/'run',lambda *args:None);w.start()
            for _ in range(100):
                result=w.poll()
                if result:break
                time.sleep(.02)
            self.assertIsNotNone(result);self.assertEqual(Path(w.result_summary.split(os.pathsep)[0]).resolve(),bindir.resolve())
            self.assertEqual(os.environ.get('PATH'),before)
