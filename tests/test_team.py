import tempfile
import unittest
from pathlib import Path
from router.team import TeamWorker

class Child:
    instances=[]
    status='reported_unverified'
    def __init__(self,command,task,directory,event):
        self.task=task; self.directory=directory; self.stopped=False; self.__class__.instances.append(self)
    def start(self): self.directory.mkdir()
    def poll(self):
        marker = '\nROUTER_REVIEW: PASS' if 'ROLE: reviewer' in self.task['goal'] else '\nROUTER_TEST: PASS' if 'ROLE: tester' in self.task['goal'] else ''
        return {'status':self.status,'summary':'observed report'+marker,'evidence':str(self.directory)}
    def stop(self,reason): self.stopped=True

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.task={'goal':'Implement goal','rules_snapshot':'immutable','project':{'path':str(self.root),'timeout':30,'idle_timeout':5}}
        Child.instances=[]; Child.status='reported_unverified'
    def tearDown(self):self.temp.cleanup()
    def worker(self):return TeamWorker(['fake'],self.task,self.root/'run',lambda *args:None,Child)
    def test_roles_do_not_claim_verified(self):
        w=self.worker();w.start()
        for _ in range(4): result=w.poll()
        self.assertEqual(result['status'],'reported_unverified')
        self.assertEqual(len(Child.instances),4)
        for child in Child.instances:
            self.assertFalse(child.task['allow_native_teams'])
            self.assertEqual(child.task['rules_snapshot'],'immutable')
        self.assertIn('observed report',Child.instances[-1].task['goal'])
        self.assertEqual(len({c.directory for c in Child.instances}),4)
    def test_final_acceptance_after_last_role(self):
        self.task['acceptance']={'files':[{'path':'answer.txt','equals':'yes'}]}
        w=self.worker();w.start()
        for _ in range(3):self.assertIsNone(w.poll())
        (self.root/'answer.txt').write_text('yes')
        self.assertEqual(w.poll()['status'],'succeeded')
        self.assertNotIn('acceptance',Child.instances[0].task)
    def test_failure_stops_pipeline(self):
        Child.status='failed';w=self.worker();w.start()
        self.assertEqual(w.poll()['status'],'failed');self.assertEqual(len(Child.instances),1)
    def test_total_deadline(self):
        w=self.worker();w.start();w.deadline=0
        self.assertEqual(w.poll()['status'],'timed_out');self.assertTrue(Child.instances[0].stopped)
    def test_failed_final_check(self):
        self.task['goal']+='\n```bridge-checks\n{"files":[{"path":"missing","exists":true}]}\n```'
        w=self.worker();w.start()
        for _ in range(4):result=w.poll()
        self.assertEqual(result['status'],'failed')
        self.assertNotIn('bridge-checks',Child.instances[0].task['goal'])

    def test_missing_review_verdict_stops_pipeline(self):
        w=self.worker();w.start();w.poll()
        w.child.poll=lambda:{'status':'reported_unverified','summary':'looks good','evidence':'x'}
        self.assertEqual(w.poll()['status'],'failed')
        self.assertEqual(len(Child.instances),2)
    def test_blocked_review_stops_pipeline(self):
        w=self.worker();w.start();w.poll()
        w.child.poll=lambda:{'status':'reported_unverified','summary':'Bug found\nROUTER_REVIEW: BLOCKED','evidence':'x'}
        self.assertEqual(w.poll()['status'],'failed')
        self.assertEqual(len(Child.instances),2)

    def test_schema_warning_requires_all_independent_gates(self):
        class WarningChild(Child):
            def poll(self):
                result=super().poll()
                result['validation_warnings']=[{'reason':'missing content rejected before execution'}]
                return result
        for with_checks in (False,True):
            with self.subTest(with_checks=with_checks):
                if with_checks:
                    self.task['acceptance']={'files':[{'path':'answer.txt','equals':'yes'}]}
                    (self.root/'answer.txt').write_text('yes')
                w=TeamWorker(['fake'],self.task,self.root/str(with_checks),lambda *args:None,WarningChild)
                w.start()
                for _ in range(4):result=w.poll()
                self.assertEqual(result['status'],'succeeded' if with_checks else 'failed')
                self.assertIn('schema warnings',result['summary'])

    def test_original_multi_project_authority_not_reparsed_as_checks(self):
        self.task['original_user_goal']='Never deploy. alpha requirements and beta requirements.\n```bridge-checks\n{"files":[{"path":"a","exists":true}]}\n```\n```bridge-checks\n{"files":[{"path":"b","exists":true}]}\n```'
        w=self.worker();w.start()
        self.assertIsNone(w.result)
        child=Child.instances[0]
        self.assertEqual(child.task['original_user_goal'],self.task['original_user_goal'])
        self.assertIn('other projects in original_user_goal are context only',child.task['goal'])
