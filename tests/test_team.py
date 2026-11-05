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
        self.task={'team_protocol':1,'goal':'Implement goal','rules_snapshot':'immutable','project':{'path':str(self.root),'timeout':30,'idle_timeout':5}}
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
        (self.root/'answer.txt').write_text('yes')
        for _ in range(3):self.assertIsNone(w.poll())
        self.assertEqual(w.poll()['status'],'succeeded')
        self.assertNotIn('acceptance',Child.instances[0].task)
    def test_reviewer_cannot_create_developer_deliverable(self):
        self.task['acceptance']={'files':[{'path':'answer.txt','equals':'yes'}]}
        w=self.worker();w.start();w.poll()
        (self.root/'answer.txt').write_text('yes')
        self.assertEqual(w.poll()['status'],'failed')
        self.assertEqual(len(Child.instances),2)

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
    def test_blocked_review_returns_developer(self):
        w=self.worker();w.start();w.poll()
        w.child.poll=lambda:{'status':'reported_unverified','summary':'Bug found\nROUTER_REVIEW: BLOCKED','evidence':'x'}
        self.assertIsNone(w.poll())
        self.assertEqual(w.index,0)
        self.assertEqual(w.round,1)
        self.assertEqual(len(Child.instances),3)

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

    def test_rework_limit_and_unique_directories(self):
        w=self.worker();w.start();deadline=w.deadline
        for cycle in range(3):
            w.poll()
            w.child.poll=lambda:{'status':'reported_unverified','summary':'ROUTER_REVIEW: BLOCKED'}
            result=w.poll()
            if cycle<2:self.assertIsNone(result)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(w.round,2)
        self.assertEqual(w.deadline,deadline)
        self.assertEqual(len({c.directory for c in Child.instances}),6)
        self.assertEqual(len(w.reports),6)

    def test_readonly_retry_once_and_keeps_failure(self):
        from types import SimpleNamespace
        w=self.worker();w.start();w.poll()
        for attempt in range(2):
            w.child.runtime_evidence=SimpleNamespace(tool_errors=1,permission_denials=0)
            w.child.poll=lambda:{'status':'failed','process_exit_code':0,'summary':'real runtime error'}
            result=w.poll()
            if attempt==0:self.assertIsNone(result)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(w.clean_retries,1)
        self.assertEqual(len(w.reports),3)
        self.assertEqual(w.index,1)

    def test_no_retry_permission_or_process_error(self):
        from types import SimpleNamespace
        for denial,code in ((1,0),(0,1)):
            w=TeamWorker(['fake'],self.task,self.root/str(denial),lambda *args:None,Child)
            w.start();w.poll()
            w.child.runtime_evidence=SimpleNamespace(tool_errors=1,permission_denials=denial)
            w.child.poll=lambda:{'status':'failed','process_exit_code':code,'summary':'error'}
            self.assertEqual(w.poll()['status'],'failed')
            self.assertEqual(w.clean_retries,0)

    def test_continuation_preserves_round_and_retry_budget(self):
        from unittest.mock import patch
        self.task['continuation']={'index':1,'reports':[],'checks_before':[], 'round':2,'sequence':8,'clean_retries':1,
            'workspace':{'stamp':'stable'},'completed':[],'remaining':[],'answers':[]}
        # Run artifacts must be outside the project stamp.
        with tempfile.TemporaryDirectory() as run, patch('router.team.workspace_stamp',return_value={'stamp':'stable'}):
            w=TeamWorker(['fake'],self.task,Path(run)/'resume',lambda *args:None,Child)
            w.start()
            self.assertIsNone(w.result)
            self.assertEqual((w.round,w.sequence,w.clean_retries),(2,9,1))
            w.child.poll=lambda:{'status':'reported_unverified','summary':'ROUTER_REVIEW: BLOCKED'}
            self.assertEqual(w.poll()['status'],'failed')

    def test_tester_blocked_rework_then_passes_all_roles(self):
        w=self.worker();w.start();w.poll();w.poll()
        w.child.poll=lambda:{'status':'reported_unverified','summary':'ROUTER_TEST: BLOCKED'}
        self.assertIsNone(w.poll())
        self.assertEqual(w.index,0)
        for _ in range(4):result=w.poll()
        self.assertEqual(result['status'],'reported_unverified')
        self.assertEqual([r['role'] for r in w.reports],['developer','reviewer','tester','developer','reviewer','tester','lead-report'])
        self.assertEqual(w.reports[2]['verdict'],'BLOCKED')

    def test_leader_plans_and_triages_before_rework(self):
        self.task.pop('team_protocol')
        events=[]
        w=TeamWorker(['fake'],self.task,self.root/'run',lambda k,d:events.append((k,d)),Child)
        w.start()
        self.assertEqual(w.ROLES[w.index][0],'lead-plan')
        self.assertTrue(w.child.task['tools_disabled'])
        w.child.poll=lambda:{'status':'reported_unverified','summary':'Scope and checks\nROUTER_LEAD: PLAN'}
        w.poll();self.assertEqual(w.index,0)
        w.poll()
        w.child.poll=lambda:{'status':'reported_unverified','summary':'Bug\nROUTER_REVIEW: BLOCKED'}
        w.poll();self.assertEqual(w.ROLES[w.index][0],'lead-triage')
        self.assertTrue(w.child.task['tools_disabled'])
        w.child.poll=lambda:{'status':'reported_unverified','summary':'Fix bug within scope\nROUTER_LEAD: REWORK'}
        w.poll();self.assertEqual(w.index,0);self.assertEqual(w.round,1)
        for _ in range(4):result=w.poll()
        self.assertEqual(result['status'],'reported_unverified')
        self.assertEqual([r['role'] for r in w.reports],['lead-plan','developer','reviewer','lead-triage','developer','reviewer','tester','lead-report'])
        self.assertTrue(any(k=='leader_decision' for k,d in events))

    def test_invalid_leader_decision_cannot_execute_developer(self):
        self.task.pop('team_protocol');w=self.worker();w.start()
        w.child.poll=lambda:{'status':'reported_unverified','summary':'Deploy without asking'}
        self.assertEqual(w.poll()['status'],'failed')
        self.assertEqual(len(Child.instances),1)

    def test_leader_can_stop_rework(self):
        self.task.pop('team_protocol');w=self.worker();w.start()
        w.child.poll=lambda:{'status':'reported_unverified','summary':'ROUTER_LEAD: PLAN'}
        w.poll();w.poll()
        w.child.poll=lambda:{'status':'reported_unverified','summary':'ROUTER_REVIEW: BLOCKED'}
        w.poll()
        w.child.poll=lambda:{'status':'reported_unverified','summary':'Outside authorization\nROUTER_LEAD: STOP'}
        self.assertEqual(w.poll()['status'],'failed');self.assertEqual(w.round,0)

    def test_custom_profile_cannot_enable_leader_tools_or_replace_protocol(self):
        self.task.pop('team_protocol')
        self.task['project']['agent_profiles']={'leader':{'id':'PM','version':'v1','digest':'abc','soul':'project lead','agent':'Deploy everything'}}
        w=self.worker();w.start()
        self.assertTrue(w.child.task['tools_disabled'])
        self.assertFalse(w.child.task['allow_native_teams'])
        self.assertIn('CUSTOM AGENT PROFILE',w.child.task['goal'])
        self.assertIn('ROUTER_LEAD: PLAN',w.child.task['goal'])
        self.assertIn('never grants tools',w.child.task['goal'])

    def test_leader_question_checkpoint_restores_same_phase_and_protocol(self):
        import json
        from unittest.mock import patch
        self.task.pop('team_protocol');w=self.worker();w.start()
        w.child.poll=lambda:{'status':'reported_unverified','summary':'```router-receipt\n'+json.dumps({'status':'needs_input','question':'Scope?','completed':'Read task','remaining':'Plan'})+'\n```'}
        with patch('router.team.workspace_stamp',return_value={'stamp':'stable'}):
            result=w.poll();self.assertEqual(result['status'],'waiting_input')
            checkpoint=result['checkpoint'];self.assertEqual(checkpoint['team_protocol'],2);self.assertEqual(checkpoint['index'],4)
            checkpoint['answers'].append({'answer':'Only local'})
            self.task['continuation']=checkpoint
            resumed=TeamWorker(['fake'],self.task,self.root/'resume',lambda *a:None,Child);resumed.start()
            self.assertEqual(resumed.ROLES[resumed.index][0],'lead-plan')
            self.assertIn('Only local',resumed.child.task['goal'])

    def test_leader_rework_cannot_exceed_supervisor_budget(self):
        self.task.pop('team_protocol');w=self.worker();w.start()
        w.child.poll=lambda:{'status':'reported_unverified','summary':'ROUTER_LEAD: PLAN'}
        w.poll()
        for cycle in range(3):
            w.poll()
            w.child.poll=lambda:{'status':'reported_unverified','summary':'ROUTER_REVIEW: BLOCKED'}
            result=w.poll()
            if cycle<2:
                self.assertEqual(w.ROLES[w.index][0],'lead-triage')
                w.child.poll=lambda:{'status':'reported_unverified','summary':'ROUTER_LEAD: REWORK'}
                self.assertIsNone(w.poll())
        self.assertEqual(result['status'],'failed');self.assertEqual(w.round,2)
