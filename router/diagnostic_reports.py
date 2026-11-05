"""Bounded, read-only task reports for the independent MM diagnostic process."""
import json
from pathlib import Path
import re

PAGE_SIZE = 3000
MAX_REPORT = 48000
LOG_BYTES = 128 * 1024


def redact(text):
    """Redact common credential formats before clipping or pagination."""
    text = str(text)
    text = re.sub(r'(?i)(?:@all|@channel|@here)', '[mention removed]', text)
    text = re.sub(r'-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)', '[REDACTED KEY]', text)
    text = re.sub(r'(?i)\bBearer\s+[^\s"\'<>]+', 'Bearer [REDACTED]', text)
    text = re.sub(r'\b(?:fh_u_|glpat-|gh[pousr]_|github_pat_)[A-Za-z0-9_.-]+', '[REDACTED]', text)
    text = re.sub(r'(?i)(https?://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', text)
    text = re.sub(r'(?i)([?&](?:token|key|secret|signature|access_token)=)[^&#\s]+', r'\1[REDACTED]', text)
    text = re.sub(r'''(?ix)(["']?(?:[\w.-]*(?:token|password|passwd|secret|api_key|apikey|authorization|cookie|credential)[\w.-]*)["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;]+)''', r'\1[REDACTED]', text)
    text = re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[REDACTED JWT]', text)
    return text


def read_tail(path):
    with path.open('rb') as stream:
        size = stream.seek(0, 2)
        stream.seek(max(0, size - LOG_BYTES))
        data = stream.read(LOG_BYTES)
    # Discard a partial line, which might begin halfway through a credential.
    if size > LOG_BYTES:
        data = data.partition(b'\n')[2]
    return data.decode('utf-8', errors='replace'), size > LOG_BYTES


def error_text(raw):
    errors = []
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        message = event.get('message')
        blocks = message.get('content', []) if isinstance(message, dict) else []
        if isinstance(blocks, list):
            for block in blocks:
                if isinstance(block, dict) and block.get('type') == 'tool_result' and block.get('is_error'):
                    content = block.get('content', '')
                    if isinstance(content, list):
                        content = '\n'.join(str(item.get('text', '')) for item in content if isinstance(item, dict) and item.get('type') == 'text')
                    errors.append(str(content))
        if event.get('type') == 'result' and event.get('is_error'):
            errors.append(str(event.get('result', 'Model execution error')))
    return '\n'.join(errors)


def report(db, database_path, task_id, page, reply_to):
    task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    if task is None:
        return '未找到任务。'
    if reply_to is not None:
        original = json.loads(task['reply_to'] or '{}')
        if (not original.get('source_user_id') or not original.get('channel_id')
                or original.get('source_user_id') != reply_to.get('source_user_id')
                or original.get('channel_id') != reply_to.get('channel_id')):
            return '详细报告仅允许原发信人在原频道查询；缺少来源记录的历史任务请在本机查看。'
    lines = ['任务 ' + task_id + ' / ' + str(task['status']),
             '保存的任务报告（模型结论需结合工具证据核验）',
             redact(task['summary'] or '尚无任务报告。')]
    # DB summaries work even when old evidence paths have moved or logs are missing.
    role_lines = []
    rows = db.execute("SELECT detail FROM events WHERE task_id=? AND kind='role_finished' ORDER BY id DESC LIMIT 12", (task_id,)).fetchall()
    for row in reversed(rows):
        try:
            value = json.loads(row['detail'])
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            role_lines.append(redact('阶段 {role} / {status} / exit={process_exit_code}\n{summary}'.format(
                role=value.get('role', 'unknown'), status=value.get('status', 'unknown'),
                process_exit_code=value.get('process_exit_code'), summary=value.get('summary', ''))))
    # Derive the only allowed directory from the validated ID, never DB evidence paths.
    runtime = Path(database_path).resolve().parent
    root = runtime / 'runs' / task_id
    if root.exists() and root.resolve() == root and not root.is_symlink():
        files = []
        for pattern in ('*.log', '*/*.log', '*/*/*.log'):
            files.extend(root.glob(pattern))
        files = [p for p in files if p.name in ('stdout.log', 'stderr.log')
                 and p.resolve() == p and not p.is_symlink() and p.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files[:24]:
            try:
                raw, clipped = read_tail(path)
                body = error_text(raw) if path.name == 'stdout.log' else raw
                if body.strip():
                    lines.append('证据 ' + str(path.relative_to(runtime)) +
                                 ('（日志尾部，前文省略）' if clipped else '') + '\n' + redact(body))
            except OSError:
                lines.append('证据文件暂不可读：' + str(path.relative_to(runtime)))
        if len(files) > 24:
            lines.append('仅扫描最近24个日志文件。')
    else:
        lines.append('本机任务日志目录缺失或路径不受信任；以上为数据库保存的报告。')
    lines.extend(role_lines)
    text = '\n\n'.join(lines)
    if len(text) > MAX_REPORT:
        text = text[:MAX_REPORT] + '\n报告达到长度上限，后续内容省略。'
    pages = max(1, (len(text) + PAGE_SIZE - 1) // PAGE_SIZE)
    if page > pages:
        return '页码超出范围，共 ' + str(pages) + ' 页。'
    result = f'【独立诊断报告】第 {page}/{pages} 页\n' + text[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    if page < pages:
        result += f'\n下一页：/diag report {task_id} {page + 1}'
    return result
