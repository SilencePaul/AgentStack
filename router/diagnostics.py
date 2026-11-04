"""Independent read-only diagnostics. No model, tool execution, or task mutations."""
import argparse
import json
import logging
from pathlib import Path
import re
import sqlite3
import threading
import time

QUERIES={'现在谁在干什么','现在谁在干活','谁在干什么','现在各个agent的状态','各个agent的状态','router状态','router还活着吗','卡在哪里','现在卡在哪里','哪里卡住了','查看agent状态','现在谁在干什么，卡在哪里','现在谁在干什么、卡在哪里','现在各个agent都在干什么','现在各个agent在干什么','各agent状态','现在router状态','现在进度怎么样','现在什么进度'}
ROLES=('developer','reviewer','tester','lead-report')


def is_diagnostic_query(text):
    text=text.strip()
    if text=='/diag' or text.startswith('/diag '):return True
    return re.sub(r'[\s？?！!。]+','',text).lower() in QUERIES


def safe_label(value):
    return re.sub(r'[^A-Za-z0-9_.:-]','',str(value or ''))[:80] or 'unknown'


def tool_activity(event):
    message=event.get('message',{})
    blocks=message.get('content',[]) if isinstance(message,dict) else []
    result=[]
    if isinstance(blocks,list):
        for block in blocks:
            if not isinstance(block,dict):continue
            if block.get('type')=='tool_use':
                result.append({'state':'started','tool':safe_label(block.get('name')),'tool_id':safe_label(block.get('id'))})
            elif block.get('type')=='tool_result':
                result.append({'state':'error' if block.get('is_error') else 'returned','tool_id':safe_label(block.get('tool_use_id'))})
    return result


class DiagnosticRouter:
    def __init__(self,path):self.path=Path(path)

    def handle(self,text,source_key=None,reply_to=None):
        parts=text.strip().split()
        task_id=None
        if parts and parts[0]=='/diag':
            if len(parts)==3 and parts[1]=='show' and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',parts[2]):task_id=parts[2]
            elif parts not in (['/diag'],['/diag','status']):
                return '只读诊断：/diag 或 /diag show 任务ID；也可问“现在谁在干什么”“卡在哪里”。不执行修复/重启/派单。'
        elif not is_diagnostic_query(text):return '请用 /diag 查询。'
        if not self.path.exists():return '诊断进程在线；尚无Router数据库。请先配置并启动Router，诊断不会代建工作库。'
        try:
            with sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1) as db:
                db.row_factory=sqlite3.Row
                return self._report(db,task_id)
        except (sqlite3.Error,ValueError,TypeError,KeyError):
            return '诊断进程在线；Router数据库暂不可读或结构不兼容。不能判断Agent当前状态；工作任务未被修改。'
        finally:
            if 'db' in locals():db.close()

    def _report(self,db,task_id):
        now=time.time()
        def meta(key,default=None):
            row=db.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else default
        beat=meta('service_heartbeat')
        age=None
        if beat=='stopped':health='已停止（Router主动记录）'
        elif beat is None:health='尚无心跳，不能确认运行'
        else:
            try:age=max(0,int(now-float(beat)))
            except (TypeError,ValueError):age=None
            health=('在线，最近心跳'+str(age)+'秒前') if age is not None and age<10 else '心跳过期或无效；可能退出/阻塞，不能确认子进程是否仍运行'
        lines=['【独立诊断】'+time.strftime('%Y-%m-%d %H:%M:%S'),'Router：'+health,
               '调度暂停：'+str(meta('paused','0')=='1'),
               '工作MM最近记录：'+str(meta('transport_health','未记录')),
               'Multica最近记录：'+str(meta('multica_health','未配置'))]
        counts=db.execute('SELECT status,COUNT(*) FROM tasks GROUP BY status').fetchall()
        lines.append('任务统计：'+(', '.join(str(r[0])+'='+str(r[1]) for r in counts) or '无任务'))
        if task_id:
            tasks=db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchall()
        else:
            tasks=db.execute("SELECT * FROM tasks WHERE status IN ('running','queued','waiting_input','failed','timed_out','interrupted') ORDER BY CASE WHEN status='running' THEN 0 WHEN status IN ('failed','timed_out','interrupted') THEN 1 ELSE 2 END,rowid DESC LIMIT 6").fetchall()
        if not tasks:lines.append('未找到任务。')
        for task in tasks:
            if task_id:
                lines+=self._task(db,task,now,beat=='stopped' or age is None or age>=10,meta)
            else:
                lines.append(safe_label(task['project_id'])+' / '+safe_label(task['id'])+'：'+safe_label(task['status']))
        if not task_id:lines.append('最多展示6个待处理任务；完成历史见监控网页。详情：/diag show 任务ID。')
        lines.append('“无输出/未返回”是观测，不等于卡死；不会读取命令参数、模型思考或自动重跑。')
        return '\n'.join(lines)

    def _task(self,db,task,now,stale,meta):
        query="SELECT id,kind,detail,CAST(strftime('%s',created_at) AS INTEGER) AS ts FROM events WHERE task_id=?"
        rows=list(db.execute(query+" AND kind IN ('role_started','role_finished','started') ORDER BY id DESC LIMIT 64",(task['id'],)))
        for role in ROLES:
            for kind,limit in (('output',1),('tool_activity',32)):
                rows+=list(db.execute(query+" AND kind=? AND json_extract(detail,'$.role')=? ORDER BY id DESC LIMIT ?",(task['id'],kind,role,limit)))
        lines=['','项目 '+safe_label(task['project_id'])+' / 任务 '+safe_label(task['id'])+'：'+safe_label(task['status'])]
        if task['status'] in ('queued','failed','interrupted','timed_out') and meta('project_paused:'+task['project_id'],False):lines.append('阻塞条件：项目被暂停；需要检查失败记录后显式恢复。')
        if stale and task['status']=='running':lines.append('注意：running是最后记录，Router心跳已失联；下面不是进程存活证明。')
        events=[]
        for row in rows:
            try:d=json.loads(row['detail'])
            except (ValueError,TypeError):continue
            if isinstance(d,dict):events.append((row['kind'],d,row['ts'] or now,row['id']))
        for role in ROLES:
            matching=sorted([e for e in events if e[1].get('role')==role],key=lambda e:e[3],reverse=True)
            boundary=next((e[3] for e in matching if e[0]=='role_started'),0)
            selected=[(k,d,t) for k,d,t,i in matching if i>=boundary]
            began=next((t for k,d,t in selected if k=='role_started'),None)
            finished=next((d.get('status','unknown') for k,d,t in selected if k=='role_finished'),None)
            # Events from worker have a nested detail; supervisor events do not.
            info=next((d.get('detail',{}) for k,d,t in selected if k=='started'),{})
            terminal=task['status'] not in ('running','queued')
            status=safe_label(finished) if finished else (('未收到阶段收尾；任务已'+safe_label(task['status'])) if began and terminal else ('执行中（最后记录）' if began else ('未启动/未执行' if task['status']!='queued' else '排队等待')))
            if finished=='reported_unverified':status='阶段已返回（任务验收另计）'
            line=role+'：'+status
            end=next((d for k,d,t in selected if k=='role_finished'),{})
            if 'process_exit_code' in end:line+='；进程退出码='+str(end['process_exit_code'])
            if info:line+='；PID='+safe_label(info.get('pid'))+'；session='+safe_label(info.get('session_id'))
            if began and not finished and not terminal:line+='；自阶段启动已过'+str(max(0,int(now-began)))+'秒'
            lines.append(line)
            output=next((t for k,d,t in selected if k=='output'),None)
            if began and not finished and not terminal:
                quiet=max(0,int(now-(output or began)))
                project=json.loads(task['project'])
                limit=project.get('idle_timeout',300)
                lines.append('  最近输出距今'+str(quiet)+'秒；静默阈值'+str(limit)+'秒'+('；已超过阈值，待监督器确认/处理' if quiet>float(limit) else ''))
            tools=[(d.get('detail',{}),t) for k,d,t in selected if k=='tool_activity']
            if tools:
                latest,when=tools[0]
                name=latest.get('tool') or next((d.get('tool') for d,t in tools if d.get('tool_id')==latest.get('tool_id') and d.get('tool')), 'unknown')
                state={'started':'已发起，尚未记录返回','returned':'已返回','error':'返回错误（不代表后续未恢复）'}.get(latest.get('state'),'未知')
                lines.append('  最近工具：'+safe_label(name)+'，'+state+'；'+str(max(0,int(now-when)))+'秒前')
            elif began and finished:lines.append('  阶段已返回；'+('汇总角色未启用工具。' if role=='lead-report' else '未记录可识别工具事件。'))
            elif began:lines.append('  暂无可识别工具事件；可能在思考/等待，也可能输出格式不支持。')
        if task['status']=='waiting_input' and 'continuation' in task.keys() and task['continuation']:
            cp=json.loads(task['continuation'])
            lines.append('等待问题：'+cp['question']+'；问题ID='+cp['question_id'])
        return lines


def main():
    from .cli import service_lock,deliver_pending
    from .store import Store
    from .mm import AiCrew,Poller
    p=argparse.ArgumentParser();p.add_argument('--config',default='config.local.json');p.add_argument('--runtime',default='.runtime');p.add_argument('--diagnostics-runtime',default='.diagnostics')
    a=p.parse_args();config=json.loads(Path(a.config).read_text(encoding='utf-8-sig'))
    settings=config.get('mm')
    if not settings:raise ValueError('MM configuration is required for diagnostics')
    diag=Path(a.diagnostics_runtime);diag.mkdir(exist_ok=True,parents=True)
    logging.basicConfig(filename=diag/'service.log',level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    stopped=threading.Event()
    with service_lock(diag/'service.lock'):
        store=Store(diag/'diagnostics.sqlite3')
        client=AiCrew(settings['script'],settings['base'],cancel_event=stopped)
        poller=Poller(client,store,DiagnosticRouter(Path(a.runtime)/'router.sqlite3'),settings['channels'],settings['allowed_user_ids'],settings['bot_id'],accept_query=is_diagnostic_query)
        print('Independent MM diagnostics online. Ctrl+C stops diagnostics only.',flush=True)
        try:
            while not stopped.is_set():
                try:
                    if client.get('/users/me').get('id')!=settings['bot_id']:raise ValueError('Diagnostic bot identity mismatch')
                    poller.poll()
                    failures=deliver_pending(store,client,stopped)
                    if failures:raise failures[0]
                    store.set_meta('diagnostic_heartbeat',str(time.time()))
                except Exception as exc:logging.warning('Diagnostic transport/read failure (%s)',type(exc).__name__)
                stopped.wait(2)
        except KeyboardInterrupt:pass
        finally:stopped.set();store.close()

if __name__=='__main__':main()
