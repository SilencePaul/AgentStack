import json
from pathlib import Path
import tempfile
import time
import unittest
from router.store import Store
from router.diagnostics import DiagnosticRouter, is_diagnostic_query, tool_activity

class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.store=Store(self.root/'router.sqlite3');self.store.add_project('p',str(self.root))
        self.task=self.store.submit('p','private goal')
        self.id=self.task['id'];self.diag=DiagnosticRouter(self.root/'router.sqlite3')
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def test_stopped_router_still_answers_without_mutating_task(self):
        self.store.set_meta('service_heartbeat','stopped')
        before=self.store.get(self.id)
        answer=self.diag.handle('现在谁在干什么')
        self.assertIn('已停止',answer)
        self.assertEqual(before,self.store.get(self.id))
    def test_stale_heartbeat_not_claimed_as_confirmed_death(self):
        self.store.set_meta('service_heartbeat',str(time.time()-60))
        self.assertIn('心跳过期',self.diag.handle('卡在哪里'))
    def test_missing_database_is_diagnosable(self):
        self.assertIn('尚无',DiagnosticRouter(self.root/'absent.db').handle('/diag'))
    def test_detailed_roles_and_tool_progress_without_payload(self):
        self.store.db.execute("UPDATE tasks SET status='running' WHERE id=?",(self.id,))
        self.store.event(self.id,'role_started',{'role':'developer'})
        self.store.event(self.id,'started',{'role':'developer','detail':{'pid':42,'session_id':'session-1'}})
        self.store.event(self.id,'tool_activity',{'role':'developer','detail':{'state':'started','tool':'Bash','tool_id':'t1'}})
        text=self.diag.handle('/diag show '+self.id)
        for expected in ('developer','reviewer','tester','lead-report','Bash','42','session-1'):
            self.assertIn(expected,text)
        self.assertNotIn('private goal',text)
    def test_tool_activity_drops_commands_and_reports_results(self):
        event={'message':{'content':[{'type':'tool_use','name':'Bash','id':'t','input':{'command':'SECRET'}},{'type':'tool_result','tool_use_id':'t','content':'PASSWORD','is_error':True}]}}
        result=tool_activity(event)
        self.assertNotIn('SECRET',json.dumps(result));self.assertNotIn('PASSWORD',json.dumps(result))
        self.assertEqual(result[1]['state'],'error')
    def test_queries_are_bounded_not_general_task_classifier(self):
        self.assertTrue(is_diagnostic_query('现在谁在干什么？'))
        self.assertTrue(is_diagnostic_query('/diag show abc'))
        self.assertFalse(is_diagnostic_query('修复现在谁在干什么页面'))
        self.assertFalse(is_diagnostic_query('/router run p 现在谁在干什么'))

    def test_many_output_events_do_not_hide_role_start(self):
        self.store.event(self.id,'role_started',{'role':'developer'})
        for _ in range(350):self.store.event(self.id,'output',{'role':'developer','detail':{'bytes':1}})
        self.assertIn('developer：执行中',self.diag.handle('/diag show '+self.id))

    def test_corrupt_database_still_returns_diagnostic_reply(self):
        p=self.root/'bad.db';p.write_text('not sqlite')
        self.assertIn('暂不可读',DiagnosticRouter(p).handle('/diag'))

    def test_mm_routing_authorization_dedup_and_restart(self):
        from unittest.mock import Mock
        from router.mm import Poller
        def post(pid,t,user,text):return dict(id=pid,create_at=t,user_id=user,channel_id='c',message=text)
        def page(*posts):return {'order':[p['id'] for p in posts],'posts':{p['id']:p for p in posts}}
        records=Store(self.root/'diag.db');c=Mock()
        records.set_meta('mm.cursor:c',{'time':10,'ids':['old']})
        posts=page(post('q',12,'human','现在谁在干什么'),post('bad',11,'stranger','/diag'),post('old',9,'human','/diag'))
        c.get.return_value=posts
        poll=Poller(c,records,self.diag,['c'],['human'],'bot',accept_query=is_diagnostic_query)
        poll.poll()
        self.assertEqual(len(records.outbox_pending()),1)
        records.close();records=Store(self.root/'diag.db')
        Poller(c,records,self.diag,['c'],['human'],'bot',accept_query=is_diagnostic_query).poll()
        self.assertEqual(len(records.outbox_pending()),1)
        records.close()
        work=Mock()
        normal=Poller(c,self.store,work,[{'channel_id':'c','dm_user':'human'}],['human'],'bot')
        self.store.set_meta('mm.cursor:c',{'time':10,'ids':['old']})
        normal.poll();work.handle.assert_not_called()

    def test_reply_delivery_works_without_router_process(self):
        from unittest.mock import Mock
        import threading
        from router.cli import deliver_pending
        separate=Store(self.root/'diag.db')
        self.store.set_meta('service_heartbeat','stopped')
        separate.enqueue_notice({'channel_id':'c','root_id':'p'},self.diag.handle('/diag'),'diag-test')
        client=Mock()
        self.assertEqual(deliver_pending(separate,client,threading.Event()),[])
        client.send.assert_called_once()
        self.assertIn('已停止',client.send.call_args.args[1])
        self.assertEqual(separate.outbox_pending(),[])
        separate.close()
