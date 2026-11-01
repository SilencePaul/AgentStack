import unittest
from unittest.mock import Mock
from router.mm import AiCrew, Poller, pages
from router.store import Store

def page(*posts):
    return {'order':[p['id'] for p in posts], 'posts':{p['id']:p for p in posts}}
def post(id, time, user='human', **kw):
    return dict(channel_id='c', id=id, create_at=time, user_id=user, message='/router status', **kw)
class Tests(unittest.TestCase):
    def test_cursor_and_authorization(self):
        s=Store(':memory:'); r=Mock(); r.handle.return_value='ok'; c=Mock()
        p=Poller(c,s,r,['c'],['human'],'bot')
        c.get.side_effect=[page(post('old',10))]; p.poll(); r.handle.assert_not_called()
        c.get.side_effect=[page(post('new',12),post('bad',11,'stranger')),page(post('old',10),post('older',9))]
        p.poll()
        r.handle.assert_called_once_with('/router status',source_key='new',reply_to={'channel_id':'c','root_id':'new','source_user_id':'human'})
        self.assertEqual(len(s.outbox_pending()),1)
        c.get.side_effect=[page(post('new',12),post('old',10))]; p.poll()
        self.assertEqual(r.handle.call_count,1); s.close()
    def test_all_pages_dedup(self):
        c=AiCrew('unused'); d=post('d',2,'bot',root_id='r'); d['message']='ok\n[router-delivery:key]'
        c.get=Mock(side_effect=[post('r',0), {'id':'bot'}, page(post('x',3)),page(d),page()]); c.call=Mock()
        self.assertEqual(c.send({'channel_id':'c','root_id':'r'},'ok','key')['id'],'d')
        c.call.assert_not_called()
    def test_incomplete_refuses(self):
        c=AiCrew('unused'); c.get=Mock(return_value={'order':['missing'],'posts':{}}); c.call=Mock()
        with self.assertRaises(RuntimeError):c.send({'channel_id':'c','root_id':'r'},'ok','key')
        c.call.assert_not_called()
    def test_send_verified(self):
        c=AiCrew('unused'); d=post('d',1,'bot',root_id='r'); d['message']='ok\n\n[router-delivery:key]'
        c.get=Mock(side_effect=[post('r',0), {'id':'bot'}, page(),page(d),page()]); c.call=Mock(return_value={'ok':True})
        c.send({'channel_id':'c','root_id':'r','dm_user':'someone'},'ok','key')
        c.call.assert_called_once_with('say','--dm','someone','ok\n\n[router-delivery:key]','--root','r')

    def test_restriction(self):
        c=Mock(); c.get.return_value={**page(), 'first_inaccessible_post_time':10}
        with self.assertRaises(RuntimeError):list(pages(c,'c',10))
        self.assertEqual(list(pages(c,'c',11)),[])
    def test_marker_spoof_does_not_dedup(self):
        c=AiCrew('unused'); spoof=post('spoof',1,root_id='r'); spoof['message']='[router-delivery:1]'
        c.get=Mock(side_effect=[post('r',0),{'id':'bot'},page(spoof),page(),page(spoof),page()]); c.call=Mock()
        with self.assertRaises(RuntimeError):c.send({'channel_id':'c','root_id':'r'},'ok',1)
        c.call.assert_called_once()

    def test_notice_failure_rolls_back_control_command(self):
        from router.service import Router
        s=Store(':memory:'); c=Mock(); router=Router(s)
        s.set_meta('mm.cursor:c', {'time':10,'ids':['old']})
        command=post('pause',11); command['message']='/router pause'
        c.get.side_effect=[page(command,post('old',9))]
        poller=Poller(c,s,router,['c'],['human'],'bot')
        original=s.enqueue_notice
        s.enqueue_notice=Mock(side_effect=RuntimeError('disk failure'))
        with self.assertRaises(RuntimeError):poller.poll()
        self.assertIsNone(s.meta('paused'))
        self.assertIsNone(s.meta('mm.processed:pause'))
        self.assertEqual(s.meta('mm.cursor:c')['time'],10)
        s.enqueue_notice=original
        c.get.side_effect=[page(command,post('old',9))]
        poller.poll()
        self.assertEqual(s.meta('paused'),'1')
        self.assertTrue(s.meta('mm.processed:pause'))
        self.assertEqual(len(s.outbox_pending()),1)
        s.close()

    def test_failure_keeps_http_redacts_credentials(self):
        from types import SimpleNamespace
        result=SimpleNamespace(returncode=1,stderr='')
        message=AiCrew._failure_metadata(['issue-comment','private payload'],result,{'http':413,'data':{'error':'Bearer abc123 token=secret fh_u_privatevalue body too large'}})
        self.assertIn('413',message)
        self.assertIn('body too large',message)
        for secret in ('abc123','fh_u_privatevalue','private payload','token=secret'):
            self.assertNotIn(secret,message)
    def test_failure_does_not_echo_non_json_stderr(self):
        from types import SimpleNamespace
        result=SimpleNamespace(returncode=1,stderr='private arguments\nUnicodeEncodeError: secret payload')
        message=AiCrew._failure_metadata(['say'],result,None)
        self.assertIn('UnicodeEncodeError',message)
        self.assertNotIn('secret payload',message)

    def test_waf_rejection_classified(self):
        from router.mm import TransportRejected
        from unittest.mock import patch
        from types import SimpleNamespace
        import json
        for body in ({'ok':False,'http':403,'data':{'_raw':'<title>WAF</title>'}}, {'ok':False,'error':'HTTP 403 <title>WAF</title>'}):
            result=SimpleNamespace(returncode=0,stdout=json.dumps(body),stderr='')
            with patch('router.mm.subprocess.run',return_value=result):
                with self.assertRaises(TransportRejected):AiCrew('fake').call('say','private content')
