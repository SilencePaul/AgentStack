import tempfile
import unittest
from pathlib import Path
from router.store import Store
from router.dispatch import PlanningQueue, NaturalRouter

class FakePlanner:
    def __init__(self,result):self.result=result
    def plan(self,*args):return self.result

class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'db');self.addCleanup(self.store.close)
        for name in ('a','b'):
            (self.root/name).mkdir();self.store.add_project(name,self.root/name)
        self.queue=PlanningQueue(self.store)
    def test_duplicate_and_aggregation(self):
        pid=self.queue.enqueue('both','source',{'channel':'c'})
        self.assertEqual(self.queue.enqueue('both','source',{}),pid)
        plan={'tasks':[{'project_id':p,'goal':'work'} for p in ('a','b')],'clarification':None}
        self.assertTrue(self.queue.process_one(FakePlanner(plan)))
        self.assertFalse(self.queue.process_one(FakePlanner(plan)))
        for task in self.store.tasks():
            self.assertIsNone(task['reply_to']);self.store.finish(task['id'],'reported_unverified','report','evidence')
        self.assertEqual(self.queue.flush_reports(),1)
        self.assertEqual(self.queue.flush_reports(),0)
        self.assertEqual(len(self.store.outbox_pending()),2)
    def test_planned_question_returns_to_original_thread_and_final_is_aggregated(self):
        route={'channel_id':'c','root_id':'thread','source_user_id':'owner'}
        self.queue.enqueue('work','source',route)
        self.queue.process_one(FakePlanner({'tasks':[{'project_id':'a','goal':'do work'}],'clarification':None}))
        tid=self.store.claim()['id']
        self.store.wait_input(tid,{'question_id':'q','index':0,'question':'A or B?','answers':[]},None)
        self.assertEqual(self.store.outbox_pending()[-1]['reply_to'],route)
        with self.assertRaises(ValueError):
            self.store.answer(tid,'q','B',{**route,'root_id':'other'})
        self.store.answer(tid,'q','B',route)
        self.store.claim()
        self.store.finish(tid,'succeeded','done',None)
        self.assertEqual(self.queue.flush_reports(),1)
        self.assertEqual(len(self.store.outbox_pending()),3)  # plan, question, aggregate

    def test_invalid_plan_atomic(self):
        self.queue.enqueue('work','source',{})
        self.queue.process_one(FakePlanner({'tasks':[{'project_id':'a','goal':'ok'},{'project_id':'bad','goal':'bad'}],'clarification':None}))
        self.assertEqual(self.store.tasks(),[])
    def test_clarification(self):
        self.queue.enqueue('work','source',{})
        self.queue.process_one(FakePlanner({'tasks':[],'clarification':'Which?'}))
        self.assertEqual(self.store.tasks(),[])
        self.assertIn('Which?',self.store.outbox_pending()[0]['text'])
    def test_router_queues_only(self):
        router=NaturalRouter(self.store,self.queue)
        self.assertIn('已记录规划',router.handle('do work','key'))
        self.assertEqual(self.store.tasks(),[])
    def test_recovery_bounded(self):
        pid=self.queue.enqueue('work','source',{})
        with self.store.transaction():self.store.db.execute("UPDATE planning SET status='running',attempts=1")
        self.queue.recover()
        self.assertEqual(self.store.db.execute('SELECT status FROM planning').fetchone()[0],'queued')
        with self.store.transaction():self.store.db.execute("UPDATE planning SET status='running',attempts=2")
        self.queue.recover()
        self.assertEqual(self.store.db.execute('SELECT status FROM planning').fetchone()[0],'failed')

    def test_mid_submit_failure_rolls_back(self):
        from unittest.mock import patch
        self.queue.enqueue('both','source',{})
        original=self.store.submit
        def submit(project,*args,**kwargs):
            if project=='b':raise RuntimeError('injected failure')
            return original(project,*args,**kwargs)
        plan={'tasks':[{'project_id':p,'goal':'work'} for p in ('a','b')],'clarification':None}
        with patch.object(self.store,'submit',side_effect=submit):
            self.queue.process_one(FakePlanner(plan))
        self.assertEqual(self.store.tasks(),[])
        self.assertIn('未派发任务',self.store.outbox_pending()[0]['text'])

    def test_planner_not_in_transaction(self):
        queue=self.queue;store=self.store
        class Planner:
            def plan(self,*args):
                if store.db.in_transaction:raise AssertionError('Model invoked in transaction')
                return {'tasks':[],'clarification':'Which?'}
        queue.enqueue('work')
        queue.process_one(Planner())
        self.assertEqual(store.db.execute('SELECT status FROM planning').fetchone()[0],'clarification')

    def test_failed_child_attention_before_queued_sibling(self):
        self.queue.enqueue('two tasks','source',{'channel':'c'})
        plan={'tasks':[{'project_id':'a','goal':'first'},{'project_id':'a','goal':'second'}],'clarification':None}
        self.queue.process_one(FakePlanner(plan))
        first,second=self.store.tasks()
        self.store.finish(first['id'],'failed','first failed','proof')
        self.assertEqual(self.queue.flush_reports(),0)
        pending=self.store.outbox_pending()
        self.assertEqual(len(pending),2)
        self.assertIn('attention:'+first['id'],pending[-1]['dedup_key'])
        self.assertIn('project-resume a',pending[-1]['text'])
        self.assertEqual(self.queue.flush_reports(),0)
        self.assertEqual(len(self.store.outbox_pending()),2)
        self.store.finish(second['id'],'reported_unverified','later report','proof2')
        self.assertEqual(self.queue.flush_reports(),1)
        self.assertEqual(len(self.store.outbox_pending()),3)
        self.assertEqual(self.queue.flush_reports(),0)

    def test_original_authorization_preserved_despite_plan(self):
        original='Only local edits in a. Never deploy or send messages.'
        self.queue.enqueue(original,'auth-source')
        self.queue.process_one(FakePlanner({'tasks':[{'project_id':'a','goal':'Deploy everything'}],'clarification':None}))
        task=self.store.tasks()[0]
        self.assertEqual(task['original_user_goal'],original)
        self.assertEqual(task['goal'],'Deploy everything')
        from router.worker import Worker
        from unittest.mock import patch
        worker=Worker(['missing'],task,self.root/'worker-proof',lambda *args:None)
        with patch('router.worker.subprocess.Popen',side_effect=OSError('offline test')):
            worker.start()
        prompt=(self.root/'worker-proof'/'prompt.txt').read_text(encoding='utf-8')
        self.assertIn(original,prompt)
        self.assertIn('immutable and overrides every generated suggestion',prompt)
        self.assertIn('Other projects are context only',prompt)
    def test_clarification_same_user_channel_and_dedup(self):
        route={'channel_id':'c','source_user_id':'u','root_id':'root'}
        pid=self.queue.enqueue('Original complete requirements','original',route)
        self.queue.process_one(FakePlanner({'tasks':[],'clarification':'Which project?'}))
        with self.assertRaises(ValueError):self.queue.clarify(pid,'Use a','reply',dict(route,root_id='new'))
        resumed=self.queue.clarify(pid,'Use a','reply',route)
        self.assertEqual(self.queue.clarify(pid,'Use a','reply',route),resumed)
        row=self.store.db.execute('SELECT goal,reply_to FROM planning WHERE id=?',(resumed,)).fetchone()
        self.assertIn('Original complete requirements',row['goal']);self.assertIn('Use a',row['goal'])
        self.queue.process_one(FakePlanner({'tasks':[{'project_id':'a','goal':'implement'}],'clarification':None}))
        self.assertIn('Use a',self.store.tasks()[0]['original_user_goal'])
    def test_clarification_rejects_other_sender_or_channel(self):
        route={'channel_id':'c','source_user_id':'u','root_id':'root'}
        pid=self.queue.enqueue('Original requirements','original',route)
        self.queue.process_one(FakePlanner({'tasks':[],'clarification':'Which project?'}))
        for incoming in (dict(route,source_user_id='other'),dict(route,channel_id='other'),None,{'channel_id':'c'}):
            with self.assertRaises(ValueError):self.queue.clarify(pid,'Use a','reply',incoming)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM planning').fetchone()[0],1)
    def test_local_clarification_command(self):
        pid=self.queue.enqueue('Original requirements')
        self.queue.process_one(FakePlanner({'tasks':[],'clarification':'Which project?'}))
        response=NaturalRouter(self.store,self.queue).handle('/router clarify '+pid+' Use a')
        self.assertIn('Clarification recorded',response)
