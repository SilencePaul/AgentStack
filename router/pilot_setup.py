"""Apply the explicit deployment preset once while the Router service lock is held."""
import json
import os
from pathlib import Path


def apply(store, config, config_path):
    preset = Path(config_path).resolve().parent / "pilot-preset.json"
    if not preset.exists():
        return
    settings = json.loads(preset.read_text(encoding="utf-8"))
    marker = "deployment:" + settings["id"]
    if store.meta(marker):
        return
    mm = config["mm"]
    if mm["bot_id"] != settings["bot_id"]:
        raise ValueError("Pilot preset requires the existing ag_liuym55 credentials")
    channel = settings["channel"]
    channels = list(mm["channels"])
    matches = [i for i, value in enumerate(channels)
               if (value.get("channel_id") if isinstance(value, dict) else value) == channel["channel_id"]]
    if len(matches) > 1:
        raise ValueError("Duplicate pilot channel entries")
    if matches:
        i = matches[0]
        channels[i] = {**(channels[i] if isinstance(channels[i], dict) else {}), **channel}
    else:
        channels.append(channel)
    users = list(mm["allowed_user_ids"])
    if settings["user_id"] not in users:
        users.append(settings["user_id"])
    project = channel["project_id"]
    workspace = preset.parent / "pilot-mention-workspace"
    workspace.mkdir(exist_ok=True)
    readme = workspace / "README.md"
    if not readme.exists():
        readme.write_text("Qoder 内网试运行：验证频道 @、只读任务和原线程结果通知。", encoding="utf-8")
    if project not in {p["id"] for p in store.projects()}:
        store.add_project(project, str(workspace.resolve()),
                          rules="Only this smoke workspace. Read-only smoke tasks. Do not access credentials or other repositories.")
    store.update_project(project, workspace_mode="copy", concurrency=2)
    config["concurrency"] = max(2, config.get("concurrency", 2))
    mm.update(channels=channels, allowed_user_ids=users)
    # Persist to the exact configuration used by this process.
    path = Path(config_path)
    backup = path.with_name(path.name + ".before-pilot-20261111")
    if not backup.exists():
        with backup.open("xb") as stream:
            stream.write(path.read_bytes())
    temporary = path.with_name(path.name + ".pilot-tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    store.set_meta(marker, True)
