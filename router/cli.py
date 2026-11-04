"""Local project administration and service entrypoint."""
import argparse
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
from .store import Store
from .service import Router,Scheduler

@contextmanager
def service_lock(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as f:
        f.seek(0)
        if os.name=='nt':
            import msvcrt
            if path.stat().st_size==0:f.write(b'0');f.flush();f.seek(0)
            try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise RuntimeError('Router service is already running') from None
        else:
            import fcntl
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield

def deliver_pending(store,client,stopping):
    from .mm import TransportRejected
    failures=[]
    for item in store.outbox_pending():
        if stopping.is_set():break
        if store.meta('outbox_blocked:'+str(item['id'])):continue
        try:
            client.send(item['reply_to'],item['text'],item.get('dedup_key') or str(item['id']))
            store.outbox_sent(item['id'])
        except TransportRejected as exc:
            with store.transaction():
                store.set_meta('outbox_blocked:'+str(item['id']),'HTTP 403 WAF rejected; requires report review')
                if not str(item.get('dedup_key','')).startswith('delivery-error:'):
                    store.enqueue_notice(item['reply_to'],'一份报告被网关拒绝，原始记录保存在本机，需要检查后处理。','delivery-error:'+str(item['id']))
            failures.append(exc)
        except Exception as exc:failures.append(exc)
    return failures

def serve(store,config,runtime,once=False):
    from .dispatch import PlanningQueue,NaturalRouter
    from .planner import Planner
    scheduler=Scheduler(store,config['qoder_command'],runtime/'runs',config.get('concurrency',2))
    stopping=threading.Event();threads=[]
    with service_lock(runtime/'service.lock'):
        store.recover();store.set_meta('shutdown','0')
        queue=PlanningQueue(store);queue.recover()
        planner=Planner(config['qoder_command'],runtime,model=config.get('router_model','Qwen3.8-Flash'),cancel_event=stopping)
        def planning():
            while not stopping.is_set():
                try:queue.process_one(planner)
                except Exception as exc:
                    logging.error('Planning loop failed (%s)',type(exc).__name__)
                    store.set_meta('planning_health','error:'+type(exc).__name__)
                stopping.wait(.5)
        def networking():
            from .mm import AiCrew,Poller
            settings=config['mm'];client=AiCrew(settings['script'],settings['base'],cancel_event=stopping)
            router=NaturalRouter(store,queue)
            poller=Poller(client,store,router,settings['channels'],settings['allowed_user_ids'],settings['bot_id'])
            delay=2
            while not stopping.is_set():
                try:
                    identity=client.get('/users/me')
                    if identity.get('id')!=settings['bot_id']:raise RuntimeError('Configured bot identity does not match credentials')
                    failures=[]
                    try:poller.poll()
                    except Exception as exc:failures.append(exc)
                    failures.extend(deliver_pending(store,client,stopping))
                    if failures:raise failures[0]
                    store.set_meta('transport_health','connected');delay=2
                except Exception as exc:
                    kind=type(exc).__name__
                    if store.meta('transport_health','')!='error:'+kind:logging.warning('MM transport failed (%s): %s',kind,str(exc)[:400])
                    store.set_meta('transport_health','error:'+kind);delay=min(delay*2,60)
                stopping.wait(delay)
        def multica_networking():
            from .mm import AiCrew
            from .multica import MulticaClient,MulticaPoller
            settings=config['multica'];client=MulticaClient(AiCrew(settings['script'],settings['base'],cancel_event=stopping))
            poller=MulticaPoller(client,store,settings['workspace'],settings['project_ids'],report_route=settings.get('report_route'))
            delay=3
            while not stopping.is_set():
                failures=[]
                try:
                    if client.get('/me')['id']!=settings['expected_user_id']:raise RuntimeError('Multica identity mismatch')
                except Exception as exc:
                    store.set_meta('multica_health','identity-error:'+type(exc).__name__);stopping.wait(15);continue
                for operation in (poller.poll,poller.flush_writebacks):
                    if stopping.is_set():break
                    try:operation()
                    except Exception as exc:failures.append(exc)
                if failures:
                    store.set_meta('multica_health','error:'+type(failures[0]).__name__)
                    logging.warning('Multica synchronization failed (%s): %s',type(failures[0]).__name__,str(failures[0].__cause__ or failures[0])[:400]);delay=min(delay*2,60)
                else:store.set_meta('multica_health','connected');delay=3
                stopping.wait(delay)
        functions=[planning]
        if config.get('mm'):functions.append(networking)
        if config.get('multica'):functions.append(multica_networking)
        for function in functions:
            thread=threading.Thread(target=function,daemon=True,name=function.__name__);thread.start();threads.append(thread)
        try:
            while store.meta('shutdown','0')!='1':
                scheduler.tick();queue.flush_reports();store.set_meta('service_heartbeat',str(time.time()))
                if once:break
                time.sleep(.5)
        except KeyboardInterrupt:pass
        finally:
            stopping.set()
            scheduler.shutdown()
            for thread in threads:thread.join(timeout=20)
            store.set_meta('service_heartbeat','stopped')

def main():
    p=argparse.ArgumentParser(description='Multi-project Qoder Router')
    p.add_argument('--config',default='config.local.json')
    p.add_argument('--runtime',default='.runtime')
    sub=p.add_subparsers(dest='action',required=True)
    a=sub.add_parser('project-add');a.add_argument('id');a.add_argument('path');a.add_argument('--rules');a.add_argument('--context');a.add_argument('--timeout',type=int,default=1800);a.add_argument('--idle-timeout',type=int,default=300)
    a=sub.add_parser('goal');a.add_argument('text')
    a=sub.add_parser('submit');a.add_argument('project');a.add_argument('goal');a.add_argument('--goal-file',action='store_true')
    a=sub.add_parser('project-update');a.add_argument('id');a.add_argument('--rules');a.add_argument('--context')
    sub.add_parser('status');sub.add_parser('projects');sub.add_parser('stop')
    a=sub.add_parser('show');a.add_argument('id')
    a=sub.add_parser('answer');a.add_argument('id');a.add_argument('question_id');a.add_argument('text')
    a=sub.add_parser('serve');a.add_argument('--once',action='store_true')
    args=p.parse_args();runtime=Path(args.runtime).resolve();runtime.mkdir(parents=True,exist_ok=True)
    rules=Path(__file__).with_name('common-rules.md').read_text(encoding='utf-8')
    store=Store(runtime/'router.sqlite3',common_rules=rules)
    router=Router(store)
    try:
        if args.action=='answer':
            task=store.get(args.id)
            if not task:raise ValueError('Unknown task')
            store.answer(args.id,args.question_id,args.text,task['reply_to'])
            print('Answer saved by local operator; checkpoint will be validated before continuation.')
        elif args.action=='project-add':
            store.add_project(args.id,str(Path(args.path).resolve()),rules=Path(args.rules).read_text(encoding='utf-8') if args.rules else '',context=Path(args.context).read_text(encoding='utf-8') if args.context else '',timeout=args.timeout,idle_timeout=args.idle_timeout)
            print('Project registered:',args.id)
        elif args.action=='project-update':
            store.update_project(args.id,rules=Path(args.rules).read_text(encoding='utf-8') if args.rules else None,context=Path(args.context).read_text(encoding='utf-8') if args.context else None)
            print('Project updated; existing task snapshots unchanged.')
        elif args.action=='goal':
            from .dispatch import PlanningQueue,NaturalRouter
            print(NaturalRouter(store,PlanningQueue(store)).handle(args.text))
        elif args.action=='submit':
            goal=Path(args.goal).read_text(encoding='utf-8') if args.goal_file else args.goal
            print(router.handle('/router run '+args.project+' '+goal))
        elif args.action in ('status','projects'):print(router.handle('/router '+args.action))
        elif args.action=='show':print(router.handle('/router show '+args.id))
        elif args.action=='stop':store.set_meta('shutdown','1');print('Shutdown requested; active work will be marked interrupted.')
        elif args.action=='serve':
            config=json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
            logging.basicConfig(filename=runtime/'service.log',level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
            serve(store,config,runtime,args.once)
    finally:
        if hasattr(store,'close'):store.close()
if __name__=='__main__':main()
