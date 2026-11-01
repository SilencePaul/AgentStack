import json
import tempfile
import unittest
from pathlib import Path
from router.planner import Planner

class PlannerTests(unittest.TestCase):
    def plan(self, value):
        with tempfile.TemporaryDirectory() as folder:
            def runner(args, **kwargs):
                self.assertIn('--tools',args)
                self.assertEqual(args[args.index('--tools')+1],'')
                self.assertEqual(kwargs['env']['QODER_AGENT_TEAMS'],'0')
                return json.dumps({'type':'result','result':json.dumps(value)})
            return Planner(['fake'],folder,runner=runner).plan('Do work',[{'id':'alpha','rules':'R','context':'C'}])
    def test_plan(self):
        data={'tasks':[{'project_id':'alpha','goal':'do this'}],'clarification':None}
        self.assertEqual(self.plan(data),data)
    def test_clarification(self):
        data={'tasks':[],'clarification':'Which project?'}
        self.assertEqual(self.plan(data),data)
    def test_invalid(self):
        for data in ({'tasks':[{'project_id':'unknown','goal':'x'}],'clarification':None}, {'tasks':[{'project_id':'alpha','goal':''}],'clarification':None}, {'tasks':[],'clarification':None}, {'tasks':[{'project_id':'alpha','goal':'x'}],'clarification':'question'}, [], {'tasks':[],'clarification':'q','extra':True}):
            with self.subTest(data=data), self.assertRaises(ValueError): self.plan(data)
    def test_empty_goal(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError): Planner([],folder).plan(' ',[])

    def test_real_runner_timeout_and_error_artifact(self):
        import sys
        with tempfile.TemporaryDirectory() as folder:
            script=Path(folder)/'fake.py'
            script.write_text('import time\ntime.sleep(20)',encoding='utf-8')
            planner=Planner([sys.executable,str(script)],folder,timeout=.1)
            with self.assertRaises(TimeoutError):
                planner.plan('Do work',[{'id':'alpha'}])
            self.assertEqual(len(list((Path(folder)/'plans').glob('*/error.json'))),1)

    def test_oversized_response(self):
        with tempfile.TemporaryDirectory() as folder:
            planner=Planner(['fake'],folder,runner=lambda *args,**kwargs:'x'*(1024*1024+1))
            with self.assertRaises(ValueError): planner.plan('Do work',[{'id':'alpha'}])

    def test_cancellation(self):
        import sys
        import threading
        import time
        with tempfile.TemporaryDirectory() as folder:
            script=Path(folder)/'fake.py'
            script.write_text('import time\ntime.sleep(20)',encoding='utf-8')
            event=threading.Event()
            planner=Planner([sys.executable,str(script)],folder,cancel_event=event)
            timer=threading.Timer(.1,event.set)
            timer.start()
            started=time.monotonic()
            try:
                with self.assertRaisesRegex(RuntimeError,'cancelled'):
                    planner.plan('Do work',[{'id':'alpha'}])
            finally:timer.join()
            self.assertLess(time.monotonic()-started,3)

    def test_corrective_attempt_for_prose_checks(self):
        calls=[]
        with tempfile.TemporaryDirectory() as folder:
            def runner(*args,**kwargs):
                calls.append(kwargs['prompt'])
                goal='bridge-checks: read file' if len(calls)==1 else 'Write hello\n```bridge-checks\n{"files":[{"path":"hello.txt","contains":"hello"}]}\n```'
                return json.dumps({'tasks':[{'project_id':'alpha','goal':goal}],'clarification':None})
            result=Planner(['fake'],folder,runner=runner).plan('Write hello.txt containing hello; use bridge-checks',[{'id':'alpha','path':folder}])
            self.assertEqual(len(calls),2)
            self.assertIn('```bridge-checks',result['tasks'][0]['goal'])

    def test_correction_attempt_bounded(self):
        calls=[]
        with tempfile.TemporaryDirectory() as folder:
            def runner(*args,**kwargs):
                calls.append(1)
                return json.dumps({'tasks':[{'project_id':'alpha','goal':'bridge-checks: just prose'}],'clarification':None})
            with self.assertRaises(ValueError):
                Planner(['fake'],folder,runner=runner).plan('use bridge-checks',[{'id':'alpha'}])
            self.assertEqual(len(calls),2)

    def test_original_checks_immutable(self):
        original={'files':[{'path':'a.txt','equals':'EXPECTED'}]}
        for weakened in ({'path':'a.txt','exists':True},{'path':'a.txt','equals':'WRONG'}):
            with self.subTest(weakened=weakened),tempfile.TemporaryDirectory() as folder:
                def runner(*args,**kwargs):
                    return json.dumps({'tasks':[{'project_id':'alpha','goal':'Do work\n```bridge-checks\n'+json.dumps({'files':[weakened]})+'\n```'}],'clarification':None})
                with self.assertRaisesRegex(ValueError,'immutable'):
                    Planner(['fake'],folder,runner=runner).plan('Do work\n```bridge-checks\n'+json.dumps(original)+'\n```',[{'id':'alpha','path':folder}])

    def test_original_checks_union_and_order(self):
        rules=[{'path':'a.txt','equals':'EXPECTED'},{'path':'b.txt','contains':'B'}]
        with tempfile.TemporaryDirectory() as folder:
            def runner(*args,**kwargs):
                return json.dumps({'tasks':[{'project_id':name,'goal':'Do work\n```bridge-checks\n'+json.dumps({'files':[rule]})+'\n```'} for name,rule in zip(('beta','alpha'),reversed(rules))],'clarification':None})
            result=Planner(['fake'],folder,runner=runner).plan('Do both\n```bridge-checks\n'+json.dumps({'files':rules})+'\n```',[{'id':'alpha','path':folder},{'id':'beta','path':folder}])
            self.assertEqual(len(result['tasks']),2)

    def test_inline_fence_layout_normalized_without_changing_values(self):
        from router.planner import normalize_check_layout
        from router.verification import Checks
        with tempfile.TemporaryDirectory() as folder:
            value='exact\ntext with "quotes" and \\slashes'
            body=json.dumps({'files':[{'path':'answer.txt','equals':value}]})
            goal='Do work:```bridge-checks\n'+body+'``` trailing prose'
            normalized=normalize_check_layout(goal)
            self.assertEqual(Checks(normalized,folder).files[0]['equals'],value)
            self.assertIn('\n```bridge-checks\n',normalized)
            self.assertIn('\n```\n trailing prose',normalized)

    def test_inline_fence_plan_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            goal='Write answer:```bridge-checks\n{"files":[{"path":"answer.txt","contains":"YES"}]}\n```'
            runner=lambda *args,**kwargs:json.dumps({'tasks':[{'project_id':'alpha','goal':goal}],'clarification':None})
            result=Planner(['fake'],folder,runner=runner).plan('Write answer.txt containing YES with bridge-checks',[{'id':'alpha','path':folder}])
            self.assertIn('\n```bridge-checks\n',result['tasks'][0]['goal'])

    def test_explicit_project_allowlist(self):
        with tempfile.TemporaryDirectory() as folder:
            runner=lambda *args,**kwargs:json.dumps({'tasks':[{'project_id':'beta','goal':'work'}],'clarification':None})
            with self.assertRaises(ValueError):
                Planner(['fake'],folder,runner=runner).plan('Work on alpha only',[{'id':'alpha'},{'id':'beta'}])

    def test_explicit_id_matches_whole_token(self):
        with tempfile.TemporaryDirectory() as folder:
            def runner(*args,**kwargs):
                self.assertIn('"allowed_project_ids": ["smoke-alpha"]',kwargs['prompt'])
                return json.dumps({'tasks':[{'project_id':'smoke-alpha','goal':'work'}],'clarification':None})
            result=Planner(['fake'],folder,runner=runner).plan('Work on smoke-alpha',[{'id':'alpha'},{'id':'smoke-alpha'}])
            self.assertEqual(result['tasks'][0]['project_id'],'smoke-alpha')

    def test_no_explicit_id_allows_context_mapping(self):
        with tempfile.TemporaryDirectory() as folder:
            runner=lambda *args,**kwargs:json.dumps({'tasks':[{'project_id':'beta','goal':'work'}],'clarification':None})
            result=Planner(['fake'],folder,runner=runner).plan('Update the billing project',[{'id':'alpha'},{'id':'beta','context':'billing'}])
            self.assertEqual(result['tasks'][0]['project_id'],'beta')
