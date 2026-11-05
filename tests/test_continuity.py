import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from router.store import Store
from router.service import Router
from router.continuity import read_receipt, workspace_stamp


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.repo=self.root/'repo';self.repo.mkdir()
        subprocess.run(['git','init',str(self.repo)],capture_output=True,check=True)
        self.store=Store(self.root/'db');self.store.add_project('p',str(self.repo))
        self.route={'channel_id':'c','root_id':'r','source_user_id':'u'}
        self.task=self.store.submit('p','original',reply_to=self.route)
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def wait(self):
        self.store.claim()
        checkpoint={'index':0,'reports':[],'question_id':'q1','question':'Which value?',
                    'completed':'Located file','remaining':'Implement',
                    'workspace':workspace_stamp(self.repo),'answers':[]}
        self.store.wait_input(self.task['id'],checkpoint,'evidence')
        return checkpoint
    def test_receipt_schema_and_duplicate(self):
        body={'status':'needs_input','question':'Which?','completed':'Read code','remaining':'Edit'}
        text='```router-receipt\n'+json.dumps(body)+'\n```'
        self.assertEqual(read_receipt(text),body)
        for invalid in (text+'\n'+text,'```router-receipt\n{}\n```',text+' trailing'):
            with self.assertRaises(ValueError):read_receipt(invalid)
    def test_receipt_allows_plain_explanation_but_not_conflicts(self):
        body={'status':'needs_input','question':'A or B?','completed':'Read code','remaining':'Wait then edit'}
        text='```router-receipt\n'+json.dumps(body)+'\n```'
        self.assertEqual(read_receipt(text+'\nWaiting on your A/B choice.'),body)
        self.assertEqual(read_receipt(text+'\nContinue after the answer.'),body)
        for tail in ('\n```python\nrun()\n```','\n~~~sh\nrun\n~~~','\nROUTER_REVIEW: PASS','\nROUTER_TEST: BLOCKED','\nROUTER_LEAD: REWORK','\n'+text):
            with self.subTest(tail=tail),self.assertRaises(ValueError):read_receipt(text+tail)
        for key,value in (('status','succeeded'),('question',''),('completed',[]),('remaining',None)):
            invalid={**body,key:value}
            with self.assertRaises(ValueError):
                read_receipt('```router-receipt\n'+json.dumps(invalid)+'\n```\nWaiting.')

    def test_wait_survives_restart_and_answer_is_bound(self):
        self.wait();self.store.recover()
        self.assertEqual(self.store.get(self.task['id'])['status'],'waiting_input')
        router=Router(self.store);cmd='/router answer '+self.task['id']+' q1 keep empty'
        for route in ({**self.route,'root_id':'other'},{**self.route,'source_user_id':'other'}):
            with self.assertRaises(ValueError):router.handle(cmd,reply_to=route)
        router.handle(cmd,reply_to=self.route)
        with self.assertRaises(ValueError):router.handle(cmd,reply_to=self.route)
        self.assertEqual(self.store.get(self.task['id'])['goal'],'original')
        self.assertEqual(self.store.get(self.task['id'])['continuation']['answers'][-1]['answer'],'keep empty')
    def test_wait_reserves_project_without_consuming_global_slot(self):
        self.wait();self.store.submit('p','next')
        other=self.root/'other';other.mkdir();self.store.add_project('other',str(other))
        self.store.submit('other','independent')
        self.assertEqual(self.store.claim(1)['project_id'],'other')
    def test_untracked_content_and_unavailable_git(self):
        f=self.repo/'a';f.write_text('one');before=workspace_stamp(self.repo)
        f.write_text('two');self.assertNotEqual(before,workspace_stamp(self.repo))
        with self.assertRaises(ValueError):workspace_stamp(self.root)
    def test_no_cross_task_context(self):
        self.store.finish(self.task['id'],'succeeded','OLD_SECRET_CONTEXT')
        t=self.store.submit('p','new')
        self.assertNotIn('OLD_SECRET_CONTEXT',t['project']['context'])

    def test_team_wait_then_continue_without_replaying_prior_roles(self):
        from router.team import TeamWorker
        class Child:
            result_summary=''
            def __init__(self,command,task,directory,event):self.task=task;self.directory=directory
            def start(self):self.directory.mkdir()
            def poll(self):
                return {'status':'reported_unverified','summary':self.result_summary}
            def stop(self,reason):pass
        t=self.store.claim();t['project']['path']=str(self.repo)
        w=TeamWorker(['fake'],t,self.root/'run1',lambda *a:None,Child);w.start()
        w.child.result_summary='ROUTER_LEAD: PLAN';w.poll()
        w.child.result_summary='developer done';w.poll()
        w.child.result_summary='```router-receipt\n'+json.dumps({'status':'needs_input','question':'Choose?','completed':'Read diff','remaining':'Review'})+'\n```'
        result=w.poll();self.assertEqual(result['status'],'waiting_input')
        self.assertEqual(result['checkpoint']['index'],1)
        self.store.wait_input(t['id'],result['checkpoint'],result['evidence'])
        self.store.answer(t['id'],result['checkpoint']['question_id'],'yes',self.route)
        t=self.store.claim()
        w2=TeamWorker(['fake'],t,self.root/'run2',lambda *a:None,Child);w2.start()
        self.assertIn('ROLE: reviewer',w2.child.task['goal'])
        self.assertIn('yes',w2.child.task['goal'])
        self.assertEqual(len(w2.reports),2)
        (self.repo/'changed').write_text('change')
        w3=TeamWorker(['fake'],t,self.root/'run3',lambda *a:None,Child);w3.start()
        self.assertEqual(w3.result['status'],'failed');self.assertIsNone(w3.child)

    def test_resumed_role_does_not_show_previous_finish(self):
        from router.diagnostics import DiagnosticRouter
        task=self.task['id']
        self.store.event(task,'role_started',{'role':'developer'})
        self.store.event(task,'role_finished',{'role':'developer','status':'reported_unverified'})
        self.store.event(task,'role_started',{'role':'developer'})
        self.store.claim()
        text=DiagnosticRouter(self.root/'db').handle('/diag show '+task)
        self.assertIn('developer：执行中',text)

    def test_failed_process_cannot_turn_into_waiting(self):
        from router.team import TeamWorker
        class BadChild:
            result_summary='```router-receipt\n'+json.dumps({'status':'needs_input','question':'Q','completed':'X','remaining':'Y'})+'\n```'
            def __init__(self,*args):pass
            def start(self):pass
            def poll(self):return {'status':'failed','summary':self.result_summary}
            def stop(self,reason):pass
        w=TeamWorker(['fake'],self.task,self.root/'failed',lambda *a:None,BadChild);w.start()
        self.assertEqual(w.poll()['status'],'failed')
