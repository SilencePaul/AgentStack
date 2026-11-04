"""Event-driven routing and deterministic supervision; no model busy-loop."""
from pathlib import Path
import logging
import json
import time
from .team import TeamWorker
LOG=logging.getLogger(__name__)

class Router:
    def __init__(self,store):self.store=store
    def handle(self,text,source_key=None,reply_to=None):
        pieces=text.strip().split(maxsplit=3)
        if not pieces or pieces[0]!='/router':
            return '请用 /router run 项目ID 目标 派单；/router projects 查看项目；/router status 查看汇总。'
        action=pieces[1] if len(pieces)>1 else 'help'
        if action=='projects':
            return '\n'.join(f"{p['id']}：{p['path']}" for p in self.store.projects()) or '尚未登记项目。请先在本机登记仓库及项目规范。'
        if action=='run':
            if len(pieces)<4 or not pieces[3].strip():raise ValueError('用法：/router run 项目ID 目标')
            t=self.store.submit(pieces[2],pieces[3],source_key,reply_to)
            return f"已记录 {t['id']}，项目 {t['project_id']}，状态 {t['status']}。将按并发限制执行。"
        if action=='answer':
            if len(pieces)<4:raise ValueError('用法：/router answer 任务ID 问题ID 答案')
            answer=pieces[3].split(maxsplit=1)
            if len(answer)!=2:raise ValueError('请提供问题ID和答案')
            self.store.answer(pieces[2],answer[0],answer[1],reply_to)
            return '回答已记录；将核对检查点后从该阶段新建会话继续，不重跑已完成阶段。'
        if action=='status':return self.summary()
        if action in ('pause','resume'):
            self.store.set_meta('paused','1' if action=='pause' else '0')
            return '已暂停新任务调度，运行中任务继续。' if action=='pause' else '已恢复调度；失败或中断的任务不会自动重跑。'
        if action=='resolve':
            if len(pieces)<4:raise ValueError('用法：/router resolve 任务ID 处理说明')
            task=self.store.get(pieces[2])
            if not task or task['status'] not in ('failed','timed_out','interrupted'):raise ValueError('只能记录失败或中断任务的处理说明')
            self.store.set_meta('incident_resolved:'+task['id'],pieces[3])
            self.store.event(task['id'],'incident_resolved',pieces[3])
            return '已记录处理说明，原始失败证据保持不变。'
        if action=='project-resume':
            if len(pieces)<3 or pieces[2] not in {p['id'] for p in self.store.projects()}:raise ValueError('请指定已登记的项目ID')
            self.store.set_meta('project_paused:'+pieces[2],False)
            return '已解除项目调度暂停；失败任务不会自动重跑。'
        if action=='show':
            if len(pieces)<3:raise ValueError('用法：/router show 任务ID')
            t=self.store.get(pieces[2])
            if not t:raise ValueError('找不到该任务')
            if reply_to:return f"项目 {t['project_id']}\n任务 {t['id']}\n状态 {t['status']}\n完整报告和证据已保存在本机。"
            return f"{t['id']} / {t['project_id']} / {t['status']}\n目标：{t['goal']}\n{t.get('summary') or '尚无完成报告'}"
        return '/router projects\n/router run 项目ID 目标\n/router status\n/router show 任务ID\n/router pause\n/router resume'
    def summary(self):
        tasks=self.store.tasks()
        beat=self.store.meta('service_heartbeat','stopped')
        try:alive=time.time()-float(beat)<10
        except (TypeError,ValueError):alive=False
        lines=['跨项目汇总（持久化任务记录）', '服务心跳：'+('在线' if alive else '未检测到在线服务'), '通信最近状态：'+str(self.store.meta('transport_health','未配置')), 'Multica：'+str(self.store.meta('multica_health','未配置')), '调度暂停：'+str(self.store.meta('paused','0')=='1')]
        for p in self.store.projects():
            items=[t for t in tasks if t['project_id']==p['id']]
            counts={}
            for t in items:counts[t['status']]=counts.get(t['status'],0)+1
            lines.append(p['id']+(' [项目暂停]' if self.store.meta('project_paused:'+p['id'],False) else '')+'：'+(', '.join(f'{k}={v}' for k,v in counts.items()) or '无任务'))
        with self.store.lock:
            if self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='planning'").fetchone():
                plans=self.store.db.execute('SELECT status,COUNT(*) AS n FROM planning GROUP BY status').fetchall()
                lines.append('目标规划：'+', '.join(str(r[0])+'='+str(r[1]) for r in plans))
            for t in tasks:
                if t['status']=='running':
                    row=self.store.db.execute("SELECT detail FROM events WHERE task_id=? AND kind='role_started' ORDER BY id DESC LIMIT 1",(t['id'],)).fetchone()
                    if row:lines.append(t['project_id']+' 当前阶段：'+str(json.loads(row[0]).get('role','unknown')))
        blocked=0
        with self.store.lock:
            for row in self.store.db.execute("SELECT key,value FROM metadata WHERE key LIKE 'outbox_blocked:%' OR key LIKE 'mc.writeback:%' OR key LIKE 'mc.intents:%'"):
                value=json.loads(row[1])
                if row[0].startswith('outbox_blocked:') and value:blocked+=1
                elif row[0].startswith('mc.writeback:') and value=='rejected':blocked+=1
                elif row[0].startswith('mc.intents:'):blocked+=sum(bool(i.get('rejected')) for i in value.values())
        lines.append('网关拒绝待处理：'+str(blocked))
        attention=[t for t in tasks if t['status'] in ('failed','timed_out','interrupted') and not self.store.meta('incident_resolved:'+t['id'])]
        if attention:
            lines.append('需要处理（不会自动重复执行）：')
            lines += [f"{t['project_id']} / {t['id']}：{t['status']}（详细诊断保存在本机）" for t in attention[-10:][::-1]]
        return '\n'.join(lines)

class Scheduler:
    def __init__(self,store,command,runtime,limit=2,worker_factory=TeamWorker):
        if not 1<=limit<=16:raise ValueError('concurrency must be between 1 and 16')
        self.store,self.command,self.runtime,self.limit=store,command,Path(runtime),limit
        self.factory=worker_factory;self.workers={}
        self.runtime.mkdir(parents=True,exist_ok=True)
    def tick(self):
        for task_id,worker in list(self.workers.items()):
            try:result=worker.poll()
            except Exception as exc:
                worker.stop('supervisor error')
                result={'status':'failed','summary':'Worker monitor failed: '+type(exc).__name__,'evidence':[]}
            if result is not None:
                if result['status']=='waiting_input':
                    self.store.wait_input(task_id,result['checkpoint'],result.get('evidence'))
                else:
                    self.store.finish(task_id,result['status'],result.get('summary',''),result.get('evidence'))
                del self.workers[task_id]
        if self.store.meta('paused','0')=='1':return
        while len(self.workers)<self.limit:
            task=self.store.claim(self.limit)
            if not task:break
            worker=None
            try:
                worker=self.factory(self.command,task,self.runtime/task['id']/('attempt-'+str(task['attempt'])),
                                    lambda kind,detail,tid=task['id']:self.store.event(tid,kind,detail))
                worker.start();self.workers[task['id']]=worker
            except Exception as exc:
                if worker:
                    try:worker.stop('start failed')
                    except Exception:pass
                self.store.finish(task['id'],'failed','Worker start failed: '+type(exc).__name__,[])
    def shutdown(self):
        for task_id,worker in list(self.workers.items()):
            try:worker.stop('router shutdown')
            except Exception:LOG.exception('Worker cleanup failed for %s',task_id)
            finally:self.store.finish(task_id,'interrupted','Router stopped; execution may have side effects. Inspect evidence before resubmission.',[])
        self.workers.clear()
