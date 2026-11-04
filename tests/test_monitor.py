import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import build_opener, ProxyHandler, Request
urlopen=build_opener(ProxyHandler({})).open
from urllib.error import HTTPError
from router.store import Store
from router.monitor import make_server
from router.diagnostics import DiagnosticRouter

class MonitorTests(unittest.TestCase):
    def test_readonly_http_survives_stopped_router_and_rejects_foreign_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'db';store=Store(db);store.add_project('p',tmp)
            task=store.submit('p','secret prompt');store.set_meta('service_heartbeat','stopped')
            before=store.get(task['id']);store.close()
            server=make_server(db,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            url='http://127.0.0.1:'+str(server.server_port)
            try:
                data=json.load(urlopen(url+'/api/status'))
                self.assertIn('已停止',data['summary']);self.assertNotIn('secret prompt',json.dumps(data))
                self.assertEqual(data['tasks'][0]['id'],task['id'])
                self.assertEqual(urlopen(url).status,200)
                for request in (Request(url,headers={'Host':'evil.example'}),Request(url,data=b'anything')):
                    with self.assertRaises(HTTPError) as caught:urlopen(request)
                    caught.exception.close()
                store=Store(db);self.assertEqual(before,store.get(task['id']));store.close()
            finally:server.shutdown();server.server_close();thread.join()
    def test_completed_history_is_not_repeated_in_mm_and_no_false_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'db';s=Store(db);s.add_project('p',tmp);t=s.submit('p','done')
            s.event(t['id'],'role_started',{'role':'lead-report'})
            s.event(t['id'],'role_finished',{'role':'lead-report','status':'reported_unverified'})
            s.finish(t['id'],'succeeded','ok');s.set_meta('project_paused:p',True)
            d=DiagnosticRouter(db)
            self.assertNotIn(t['id'],d.handle('/diag'))
            detail=d.handle('/diag show '+t['id'])
            self.assertNotIn('阻塞条件',detail);self.assertNotIn('可能在思考',detail)
            s.close()
