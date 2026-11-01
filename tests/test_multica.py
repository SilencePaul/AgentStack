import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from router.multica import MulticaPoller, MulticaClient
from router.store import Store

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=Store(':memory:')
        self.store.add_project('p', self.tmp.name)
        self.issue={'id':'issue1','workspace_id':'ws','status':'todo','title':'goal','description':'[router-project:p]\nTask'}
        self.client=Mock(); self.client.get.return_value=[{'id':'ws','slug':'test'}]
        self.client.issues.return_value=[self.issue]
        self.client.issue.side_effect=lambda *args:dict(self.issue)
        self.client.status.side_effect=lambda ws,iid,value:self.issue.update(status=value)
        self.adapter=MulticaPoller(self.client,self.store,'test',['p'])
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def test_only_marked_scope_and_dedup(self):
        self.adapter.poll();self.adapter.poll()
        self.assertEqual(len(self.store.tasks()),1)
        self.assertEqual(self.store.tasks()[0]['source_key'],'mc:issue1')
        self.assertIsNone(self.store.tasks()[0]['reply_to'])
        self.assertEqual(self.issue['status'],'in_progress')
    def test_unmarked_never_enqueues(self):
        self.issue['description']='normal production task'
        self.adapter.poll();self.assertEqual(self.store.tasks(),[])
    def test_failed_claim_retries_before_enqueue(self):
        self.client.status.side_effect=RuntimeError('network')
        with self.assertRaises(RuntimeError):self.adapter.poll()
        self.assertEqual(self.store.tasks(),[])
        self.client.status.side_effect=lambda ws,iid,value:self.issue.update(status=value)
        self.adapter.poll();self.assertEqual(len(self.store.tasks()),1)
    def test_success_writeback_retry_no_reexecution(self):
        self.adapter.poll();task=self.store.tasks()[0]
        self.store.finish(task['id'],'succeeded','verified','evidence')
        self.client.comment.side_effect=RuntimeError('network')
        with self.assertRaises(RuntimeError):self.adapter.flush_writebacks()
        self.assertEqual(self.issue['status'],'in_progress')
        self.client.comment.side_effect=None
        self.adapter.flush_writebacks();self.assertEqual(self.issue['status'],'done')
        self.adapter.poll();self.assertEqual(len(self.store.tasks()),1)
    def test_unverified_leaves_open(self):
        self.adapter.poll();task=self.store.tasks()[0]
        self.store.finish(task['id'],'reported_unverified','not checked')
        self.adapter.flush_writebacks();self.assertEqual(self.issue['status'],'in_progress')
        self.assertIn('NEEDS ATTENTION',self.client.comment.call_args.args[2])
    def test_scope_mismatch(self):
        self.issue['workspace_id']='other'
        with self.assertRaises(RuntimeError):self.adapter.poll()
        self.assertEqual(self.store.tasks(),[])
    def test_client_pages(self):
        crew=Mock();crew.call.side_effect=[{'data':{'issues':[{'id':'a'}],'total':2}},{'data':{'issues':[{'id':'b'}],'total':2}}]
        self.assertEqual(len(MulticaClient(crew).issues('test')),2)
        self.assertIn('offset=100',crew.call.call_args.args[1])
    def test_comment_dedup_author(self):
        crew=Mock();crew.call.side_effect=[{'data':{'id':'me'}},{'data':[{'author_id':'me','content':'[router-delivery:key]'}]}]
        MulticaClient(crew).comment('test','issue','text','key')
        self.assertEqual(crew.call.call_count,2)

    def test_edited_issue_not_closed(self):
        self.adapter.poll();task=self.store.tasks()[0]
        self.store.finish(task['id'],'succeeded','verified')
        self.issue['description'] += ' changed requirements'
        with self.assertRaises(RuntimeError):self.adapter.flush_writebacks()
        self.assertEqual(self.issue['status'],'in_progress')
    def test_external_done_not_overwritten_or_claimed(self):
        self.adapter.poll();task=self.store.tasks()[0]
        self.store.finish(task['id'],'succeeded','verified');self.issue['status']='done'
        with self.assertRaises(RuntimeError):self.adapter.flush_writebacks()
        self.assertIsNone(self.store.meta('mc.writeback:'+task['id']))
    def test_report_notice_deduplicated(self):
        self.adapter.report_route={'channel_id':'c','root_id':'r'}
        self.adapter.poll();task=self.store.tasks()[0]
        self.store.finish(task['id'],'failed','failed checks')
        self.adapter.flush_writebacks();self.adapter.flush_writebacks()
        self.assertEqual(len(self.store.outbox_pending()),1)
    def test_null_description_ignored(self):
        self.issue['description']=None
        self.adapter.poll();self.assertEqual(self.store.tasks(),[])

    def test_pending_intent_rechecks_current_allowed_projects(self):
        self.client.status.side_effect=RuntimeError('network')
        with self.assertRaises(RuntimeError):self.adapter.poll()
        self.client.reset_mock()
        self.adapter.project_ids=set()
        with self.assertRaises(RuntimeError):self.adapter.poll()
        self.client.comment.assert_not_called()
        self.client.status.assert_not_called()
        self.assertEqual(self.store.tasks(),[])
    def test_pending_intent_rechecks_registered_projects(self):
        self.client.status.side_effect=RuntimeError('network')
        with self.assertRaises(RuntimeError):self.adapter.poll()
        self.client.reset_mock()
        self.store.projects=lambda:[]
        with self.assertRaises(RuntimeError):self.adapter.poll()
        self.client.comment.assert_not_called()
        self.client.status.assert_not_called()
        self.assertEqual(self.store.tasks(),[])

    def test_report_excludes_model_text_and_paths(self):
        self.adapter.report_route={'channel_id':'c','root_id':'r'}
        self.adapter.poll();task=self.store.tasks()[0]
        self.store.finish(task['id'],'succeeded','UNTRUSTED_MODEL_TRANSCRIPT',{'path':'PRIVATE_LOCAL_PATH'})
        self.adapter.flush_writebacks()
        comment=self.client.comment.call_args.args[2]
        notice=self.store.outbox_pending()[0]['text']
        for text in (comment,notice):
            self.assertNotIn('UNTRUSTED_MODEL_TRANSCRIPT',text)
            self.assertNotIn('PRIVATE_LOCAL_PATH',text)
            self.assertIn(task['id'],text)
            self.assertIn('Project: p',text)
        self.assertEqual(self.store.meta('mc.report:'+task['id'])['version'],2)
    def test_rejected_writeback_requires_review(self):
        from router.mm import TransportRejected
        self.adapter.poll();task=self.store.tasks()[0]
        self.store.finish(task['id'],'succeeded','verified')
        self.client.comment.side_effect=TransportRejected('HTTP 403 WAF')
        with self.assertRaises(RuntimeError):self.adapter.flush_writebacks()
        self.assertEqual(self.store.meta('mc.writeback:'+task['id']),'rejected')
        self.client.comment.reset_mock();self.adapter.flush_writebacks()
        self.client.comment.assert_not_called()

    def test_rejected_claim_only_attempted_once_and_alerted(self):
        from router.mm import TransportRejected
        self.adapter.report_route={'channel_id':'c','root_id':'r'}
        self.client.comment.side_effect=TransportRejected('HTTP 403 WAF')
        with self.assertRaises(RuntimeError):self.adapter.poll()
        self.adapter.poll()
        self.assertEqual(self.client.comment.call_count,1)
        self.client.status.assert_not_called()
        self.assertEqual(self.store.tasks(),[])
        self.assertTrue(self.store.meta(self.adapter.index_key)['issue1']['rejected'])
        self.assertEqual(self.store.meta('mc.rejection_count'),1)
        self.assertEqual(len(self.store.outbox_pending()),1)
        self.assertEqual(self.store.outbox_pending()[0]['dedup_key'],'mc-claim-rejected:issue1')
    def test_rejected_writeback_alert_once(self):
        from router.mm import TransportRejected
        self.adapter.report_route={'channel_id':'c','root_id':'r'}
        self.adapter.poll();task=self.store.tasks()[0]
        self.store.finish(task['id'],'succeeded','verified')
        self.client.comment.side_effect=TransportRejected('HTTP 403 WAF')
        with self.assertRaises(RuntimeError):self.adapter.flush_writebacks()
        self.adapter.flush_writebacks()
        alerts=[n for n in self.store.outbox_pending() if n['dedup_key'].startswith('mc-writeback-rejected:')]
        self.assertEqual(len(alerts),1)
        self.assertEqual(self.store.meta('mc.rejection_count'),1)
        self.assertEqual(self.issue['status'],'in_progress')
