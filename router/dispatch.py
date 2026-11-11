"""Durable tool-free planning queue; call process_one in a separate planning thread."""
import json
import uuid
from .planner import Planner
from .service import Router


class PlanningQueue:
    def __init__(self,store):
        self.store=store
        with store.transaction():
            store.db.execute('CREATE TABLE IF NOT EXISTS planning(id TEXT PRIMARY KEY,goal TEXT NOT NULL,source_key TEXT UNIQUE,reply_to TEXT,status TEXT NOT NULL,result TEXT,error TEXT,attempts INTEGER NOT NULL DEFAULT 0,reported INTEGER NOT NULL DEFAULT 0)')

    def enqueue(self,goal,source_key=None,reply_to=None):
        if not isinstance(goal,str) or not goal.strip() or len(goal)>100000:
            raise ValueError('Goal must contain 1-100000 nonblank characters')
        with self.store.transaction():
            if source_key is not None:
                row=self.store.db.execute('SELECT id FROM planning WHERE source_key=?',(source_key,)).fetchone()
                if row:return row['id']
            pid=uuid.uuid4().hex
            self.store.db.execute("INSERT INTO planning(id,goal,source_key,reply_to,status) VALUES (?,?,?,?,'queued')",(pid,goal,source_key,json.dumps(reply_to) if reply_to is not None else None))
            return pid

    def clarify(self, plan_id, supplement, source_key=None, reply_to=None):
        with self.store.transaction():
            row = self.store.db.execute('SELECT * FROM planning WHERE id=?', (plan_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown planning ID')
            original_route = json.loads(row['reply_to']) if row['reply_to'] is not None else None
            if original_route is None:
                authorized = reply_to is None
            else:
                authorized = (isinstance(reply_to, dict) and bool(original_route.get('source_user_id'))
                    and reply_to.get('source_user_id') == original_route['source_user_id']
                    and bool(original_route.get('root_id'))
                    and reply_to.get('root_id') == original_route['root_id']
                    and reply_to.get('channel_id') == original_route.get('channel_id'))
            if not authorized:
                raise ValueError('Clarification must come from the original user in the original thread')
            if source_key is not None:
                previous = self.store.db.execute('SELECT id FROM planning WHERE source_key=?', (source_key,)).fetchone()
                if previous:
                    return previous['id']
            if row['status'] != 'clarification':
                raise ValueError('This planning request is not awaiting clarification')
            if not isinstance(supplement, str) or not supplement.strip():
                raise ValueError('A complete clarification is required')
            goal = row['goal'] + '\n\nUSER CLARIFICATION (same authorized user):\n' + supplement
            continued = self.enqueue(goal, source_key, original_route)
            self.store.db.execute("UPDATE planning SET status='clarified',result=? WHERE id=?",
                                  (json.dumps({'continued_by': continued}), plan_id))
            return continued

    def _notice(self,row,text,suffix):
        if row['reply_to'] is not None:
            self.store._notice(json.loads(row['reply_to']),text,'plan:'+row['id']+':'+suffix)

    def process_one(self,planner):
        with self.store.transaction():
            row=self.store.db.execute("SELECT * FROM planning WHERE status='queued' AND attempts<2 ORDER BY rowid LIMIT 1").fetchone()
            if row is None:return False
            row=dict(row)
            self.store.db.execute("UPDATE planning SET status='running',attempts=attempts+1 WHERE id=?",(row['id'],))
        # Never hold the store lock across model calls.
        try:
            result=planner.plan(row['goal'],self.store.projects())
            with self.store.transaction():
                if getattr(planner, "cancel_event", None) is not None and planner.cancel_event.is_set():
                    raise RuntimeError("Planning cancelled; no tasks dispatched")
                Planner.validate(result,{p['id'] for p in self.store.projects()})
                if result['clarification'] is not None:
                    self.store.db.execute("UPDATE planning SET status='clarification',result=? WHERE id=?",(json.dumps(result),row['id']))
                    self._notice(row,'规划 '+row['id']+' 需要补充信息：'+result['clarification'],'clarification')
                else:
                    task_ids=[]
                    for index,task in enumerate(result['tasks']):
                        created=self.store.submit(task['project_id'],task['goal'],'plan:'+row['id']+':'+str(index),None,original_user_goal=row['goal'])
                        task_ids.append(created['id'])
                    saved=dict(result,task_ids=task_ids)
                    self.store.db.execute("UPDATE planning SET status='dispatched',result=? WHERE id=?",(json.dumps(saved),row['id']))
                    # Dispatch progress is visible in the workbench; MM receives the result.
        except Exception as exc:
            with self.store.transaction():
                error=str(exc)[:2000]
                self.store.db.execute("UPDATE planning SET status='failed',error=? WHERE id=?",(error,row['id']))
                self._notice(row,'规划 '+row['id']+' 失败，未派发任务：'+error,'failed')
        return True

    def recover(self):
        with self.store.transaction():
            rows=self.store.db.execute("SELECT * FROM planning WHERE status='running'").fetchall()
            for row in rows:
                if row['attempts']<2:
                    self.store.db.execute("UPDATE planning SET status='queued' WHERE id=?",(row['id'],))
                else:
                    error='规划已中断两次，未派发项目任务。请确认后重新提交。'
                    self.store.db.execute("UPDATE planning SET status='failed',error=? WHERE id=?",(error,row['id']))
                    self._notice(row,error,'failed')
            return len(rows)

    def flush_reports(self):
        count=0
        terminal={'succeeded','reported_unverified','failed','timed_out','interrupted','cancelled'}
        with self.store.transaction():
            rows=self.store.db.execute("SELECT * FROM planning WHERE status='dispatched' AND reported=0").fetchall()
            for row in rows:
                saved=json.loads(row['result'])
                tasks=[self.store.get(tid) for tid in saved['task_ids']]
                for task in tasks:
                    if all(t and t['status'] in terminal for t in tasks):
                        break
                    if task is None or task['status'] not in ('failed','timed_out','interrupted'):
                        continue
                    queued = [t for t in tasks if t is not None and t['project_id'] == task['project_id'] and t['status'] == 'queued']
                    paused = self.store.meta('project_paused:'+task['project_id'],False)
                    alert = ('需要处理：'+task['project_id']+' / '+task['id']+' / '+task['status']+
                             '\n项目暂停：'+str(bool(paused))+'；同项目等待任务数：'+str(len(queued))+
                             '\n脱敏报告：/diag report '+task['id']+
                             '\n失败任务不会自动重跑。确认后可用 /router project-resume '+task['project_id']+' 恢复等待任务调度。')
                    self._notice(row,alert,'attention:'+task['id'])
                if not tasks or any(t is None or t['status'] not in terminal for t in tasks):continue
                lines=['跨项目汇总报告：'+row['id']]
                for task in tasks:
                    lines.append('\n'+task['project_id']+' / '+task['id']+' / '+task['status'])
                    lines.append('脱敏报告与工具报错：/diag report '+task['id'])
                self._notice(row,'\n'.join(lines)[:40000],'report')
                self.store.db.execute("UPDATE planning SET reported=1,status='completed' WHERE id=?",(row['id'],))
                count+=1
        return count


class NaturalRouter(Router):
    def __init__(self,store,queue):
        super().__init__(store)
        self.queue=queue

    def handle(self,text,source_key=None,reply_to=None):
        stripped=text.strip()
        pieces=stripped.split(maxsplit=2)
        if len(pieces)>=2 and pieces[:2]==['/router','clarify']:
            rest = pieces[2].split(maxsplit=1) if len(pieces)==3 else []
            if len(rest)!=2:raise ValueError('Usage: /router clarify PLANID COMPLETE CLARIFICATION')
            pid=self.queue.clarify(rest[0],rest[1],source_key,reply_to)
            return 'Clarification recorded; continued planning: '+pid
        if len(pieces)>=2 and pieces[:2]==['/router','goal']:
            if len(pieces)!=3:raise ValueError('Usage: /router goal YOUR GOAL')
            goal=pieces[2]
        elif stripped and not stripped.startswith('/'):
            goal=stripped
        else:
            return super().handle(text,source_key,reply_to)
        pid=self.queue.enqueue(goal,source_key,reply_to)
        return '已记录规划：'+pid+'。项目映射明确后将自动派单；信息不足时会请你补充。'
