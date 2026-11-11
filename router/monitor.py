"""Independent loopback-only, read-only task monitor. No model calls or commands."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit, parse_qs
from .diagnostics import DiagnosticRouter
from .dashboard import dashboard


def snapshot(path, task_id=None):
    diag=DiagnosticRouter(path)
    data={'summary':diag.handle('/diag'),'tasks':[]}
    if Path(path).exists():
        try:
            with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=1) as db:
                db.row_factory=sqlite3.Row
                data.update(dashboard(db))
                if task_id:
                    row=db.execute('SELECT summary,evidence FROM tasks WHERE id=?',(task_id,)).fetchone()
                    if row:data['result']={'summary':row['summary'] or '', 'evidence':json.loads(row['evidence']) if row['evidence'] else None}
            db.close()
        except sqlite3.Error:pass
    if task_id:data['detail']=diag.handle('/diag show '+task_id)
    return data


def make_server(path, port=8787):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get('Host') not in ('127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port)):
                self.send_error(403);return
            url=urlsplit(self.path)
            if url.path=='/':
                body=Path(__file__).with_name('monitor.html').read_bytes();kind='text/html; charset=utf-8'
            elif url.path=='/api/status':
                task_id=parse_qs(url.query).get('task',[''])[0]
                if task_id and not re.fullmatch('[a-f0-9]{32}',task_id):self.send_error(400);return
                body=json.dumps(snapshot(path,task_id or None),ensure_ascii=False).encode();kind='application/json; charset=utf-8'
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',kind)
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'")
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--runtime',default='.runtime');parser.add_argument('--port',type=int,default=8787)
    from .workbench import make_server as workbench_server
    args=parser.parse_args();server=workbench_server(Path(args.runtime)/'router.sqlite3',args.port)
    print('AgentStack workbench: http://127.0.0.1:'+str(server.server_port)+' (this machine only)',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
