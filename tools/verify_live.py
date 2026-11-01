"""Read-only verification of the deployed integration smoke; never sends messages."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from router.store import Store
from router.mm import AiCrew
from router.multica import MulticaClient

def verify(source,issue_id):
    config=json.loads(Path('config.local.json').read_text(encoding='utf-8'))
    store=Store('.runtime/router.sqlite3')
    try:
        row=store.db.execute('SELECT * FROM planning WHERE source_key=?',(source,)).fetchone()
        assert row and row['status']=='completed' and row['reported']==1,'Natural plan has not completed'
        plan=json.loads(row['result']);tasks=[store.get(t) for t in plan['task_ids']]
        assert len(tasks)==2 and all(t['status']=='succeeded' for t in tasks),'Two project teams must pass'
        sessions=set();runs=[]
        for task in tasks:
            directory=Path(task['evidence']);result=json.loads((directory/'result.json').read_text(encoding='utf-8'))
            assert [r['role'] for r in result['roles']]==['developer','reviewer','tester','lead-report']
            check=json.loads((directory/'verification.json').read_text(encoding='utf-8'))
            assert check['after'] and all(item[0] for item in check['after'])
            for role in result['roles']:
                for line in (Path(role['evidence'])/'stdout.log').read_text(encoding='utf-8').splitlines():
                    try:event=json.loads(line)
                    except ValueError:continue
                    if isinstance(event,dict) and event.get('type')=='system' and event.get('subtype')=='init':sessions.add(event['session_id'])
            runs.append({'project':task['project_id'],'task_id':task['id'],'status':task['status'],'roles':4,'checks':check['after']})
        assert len(sessions)==8,'Expected eight independent role sessions'
        mmconfig=config['mm'];mm=AiCrew(mmconfig['script'],mmconfig['base'])
        thread=mm.get('/posts/'+source+'/thread')
        marker='[router-delivery:plan:'+row['id']+':report]'
        replies=[p for p in thread['posts'].values() if p['user_id']==mmconfig['bot_id'] and p['message'].endswith(marker)]
        assert len(replies)==1,'Consolidated MM report absent or duplicated'
        mcconfig=config['multica'];mc=MulticaClient(AiCrew(mcconfig['script'],mcconfig['base']))
        issue=mc.issue(mcconfig['workspace'],issue_id);assert issue['status']=='done'
        mapped=[t for t in store.tasks() if t['source_key']=='mc:'+issue_id]
        assert len(mapped)==1 and mapped[0]['status']=='succeeded','Issue must trigger exactly one successful task'
        comments=mc.get('/issues/'+issue_id+'/comments?workspace_slug='+mcconfig['workspace'])
        result_marker='[router-delivery:mc-result:'+mapped[0]['id']+']'
        assert sum(c['content'].endswith(result_marker) for c in comments)==1,'Result comment absent or duplicated'
        assert store.meta('mc.writeback:'+mapped[0]['id'])=='sent'
        assert not store.outbox_pending(),'Pending notifications remain'
        result={'natural_source_post':source,'plan_id':row['id'],'projects':runs,'independent_sessions':len(sessions),'mm_report_post':replies[0]['id'],'multica_workspace':mcconfig['workspace'],'multica_issue':issue.get('identifier'),'multica_status':issue['status'],'multica_task_count':len(mapped),'multica_result_comments':1,'pending_outbox':0}
        Path('.runtime/overall-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result
    finally:store.close()
if __name__=='__main__':print(json.dumps(verify(sys.argv[1],sys.argv[2]),ensure_ascii=False,indent=2))
