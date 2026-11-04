"""Two real processes with local fake MM transport; no company network access."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

class IndependentProcessTest(unittest.TestCase):
    def test_diagnostics_replies_after_router_process_is_killed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);messages=root/'messages.json';messages.write_text('[]')
            fake=root/'crew.py'
            fake.write_text('''import json,sys,time
from pathlib import Path
p=Path('''+repr(str(messages))+''')
a=sys.argv[1:]
if a[:1]==['--base']:a=a[2:]
posts=json.loads(p.read_text())
if a[0]=='mm-get':
 path=a[1]
 if path=='/users/me':data={'id':'bot'}
 elif path.startswith('/posts/'):data=next(x for x in posts if x['id']==path.split('/')[-1])
 else:
  batch=sorted(posts,key=lambda x:x['create_at'],reverse=True)
  if '&before=' in path:
   before=path.split('&before=')[1];batch=batch[next(i for i,x in enumerate(batch) if x['id']==before)+1:]
  data={'order':[x['id'] for x in batch],'posts':{x['id']:x for x in batch}}
 print(json.dumps({'ok':True,'data':data}))
elif a[0]=='say':
 posts.append({'id':'reply','user_id':'bot','channel_id':'c','root_id':a[a.index('--root')+1],'message':a[2],'create_at':int(time.time()*1000)})
 temp=p.with_suffix('.tmp');temp.write_text(json.dumps(posts));temp.replace(p)
 print(json.dumps({'ok':True}))
''')
            cfg=root/'config.json';cfg.write_text(json.dumps({'qoder_command':[sys.executable],'mm':{'script':str(fake),'base':'fake','bot_id':'bot','channels':['c'],'allowed_user_ids':['human']}}))
            env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            diag=subprocess.Popen([sys.executable,'-m','router.diagnostics','--config',str(cfg),'--runtime',str(root/'work'),'--diagnostics-runtime',str(root/'diag')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            router=subprocess.Popen([sys.executable,'-m','router.cli','--config',str(cfg),'--runtime',str(root/'work'),'serve'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            def wait_for(check,seconds=15):
                until=time.monotonic()+seconds
                while time.monotonic()<until:
                    if check():return
                    time.sleep(.1)
                self.fail('process condition timed out')
            def ready():
                path=root/'diag/diagnostics.sqlite3'
                if not path.exists():return False
                import sqlite3
                from contextlib import closing
                try:
                    with closing(sqlite3.connect(path)) as db:return db.execute("SELECT 1 FROM metadata WHERE key='mm.cursor:c'").fetchone() is not None
                except sqlite3.Error:return False
            try:
                wait_for(ready)
                wait_for(lambda:(root/'work/router.sqlite3').exists())
                router.terminate();router.wait(timeout=5)
                # Wait for the real last heartbeat to become stale; no state forgery.
                time.sleep(10.5)
                messages.write_text(json.dumps([{'id':'query','channel_id':'c','user_id':'human','create_at':int(time.time()*1000),'message':'现在谁在干什么'}]))
                wait_for(lambda:any(p['user_id']=='bot' for p in json.loads(messages.read_text())))
                reply=next(p for p in json.loads(messages.read_text()) if p['user_id']=='bot')
                self.assertIn('心跳过期',reply['message'])
                self.assertIsNone(diag.poll())
            finally:
                for process in (router,diag):
                    if process.poll() is None:process.terminate()
                    process.wait(timeout=5)
