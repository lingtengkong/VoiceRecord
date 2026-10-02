from __future__ import annotations

import json
import os
import re
import uuid
import time
import tempfile
import shutil
import stat
from datetime import datetime
from pathlib import Path


def data_root() -> Path:
    # Deliberately outside Documents/OneDrive. Override used by isolated tests.
    override = os.environ.get("MEETING_SCRIBE_DATA")
    config_file = Path(__file__).resolve().parent.parent / "local_paths.json"
    configured = read_json(config_file).get("data_root")
    root = Path(override or configured) if (override or configured) else Path.home() / "MeetingScribeData"
    root.mkdir(parents=True, exist_ok=True)
    return root


def atomic_json(path: Path, value: dict | list) -> None:
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    # Windows readers briefly hold the old file without FILE_SHARE_DELETE.
    # Retry replacement rather than treating a reader race as a recording failure.
    for attempt in range(25):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 24:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(.01)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default


def recordings_root() -> Path:
    configured = read_json(data_root() / "recording_location.json").get("current")
    return Path(configured) if configured else data_root() / "recordings"


def set_recordings_root(folder: Path) -> None:
    folder = folder.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    # Check actual write access before persisting the choice.
    with tempfile.TemporaryFile(dir=folder) as probe:
        probe.write(b"Meeting Scribe")
        probe.flush()
    config_path = data_root() / "recording_location.json"
    previous = read_json(config_path).get("previous", [])
    old = str(recordings_root())
    if old not in previous:
        previous.append(old)
    atomic_json(config_path, {"current": str(folder), "previous": previous})


def new_session(title: str, root: Path | None = None) -> Path:
    root = root or recordings_root()
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")[:60] or "未命名会议"
    folder = root / f"{datetime.now():%Y%m%d_%H%M%S}_{clean}_{uuid.uuid4().hex[:6]}"
    folder.mkdir(parents=True)
    atomic_json(folder / "session.json", {
        "version": 1, "title": title.strip() or "未命名会议",
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "preparing", "privacy": "local_only", "duration": 0,
    })
    return folder


def sessions() -> list[Path]:
    config = read_json(data_root() / "recording_location.json")
    roots = {recordings_root(), data_root() / "recordings"}
    roots.update(Path(p) for p in config.get("previous", []))
    found = set()
    for root in roots:
        try:
            found.update(p.parent for p in root.glob("*/session.json"))
        except OSError:
            continue  # A previously selected removable drive may be offline.
    return sorted(found, key=lambda p: p.name, reverse=True)


def timestamp(seconds: float, milliseconds: bool = False) -> str:
    ms = max(0, round(seconds * 1000))
    sec, milli = divmod(ms, 1000)
    minute, second = divmod(sec, 60)
    hour, minute = divmod(minute, 60)
    result = f"{hour:02}:{minute:02}:{second:02}"
    return result + f",{milli:03}" if milliseconds else result


def _editable_session(folder: Path) -> Path:
    folder = Path(folder).absolute()
    config = read_json(data_root() / "recording_location.json")
    roots = {recordings_root().resolve(), (data_root() / "recordings").resolve()}
    roots.update(Path(p).resolve() for p in config.get("previous", []))
    if (folder.is_symlink() or folder.is_junction()
            or folder.resolve().parent not in roots
            or not (folder / "session.json").is_file()):
        raise ValueError("只能操作保存目录中的会议文件夹。")
    return folder.resolve()


def rename_session(folder: Path, title: str) -> None:
    title = title.strip()
    if not title:
        raise ValueError("会议名称不能为空。")
    folder = _editable_session(folder)
    meta = read_json(folder / "session.json")
    if not isinstance(meta, dict) or not meta:
        raise ValueError("会议信息无法读取，未修改名称。")
    meta["title"] = title
    atomic_json(folder / "session.json", meta)


def delete_session(folder: Path) -> None:
    folder = _editable_session(folder)
    # Refuse linked entries before deleting anything; never follow them outside the session.
    for parent, dirs, files in os.walk(folder, followlinks=False):
        for name in dirs + files:
            entry = Path(parent) / name
            if entry.is_symlink() or entry.is_junction():
                raise ValueError("会议文件夹包含链接，请在文件管理器中检查后删除。")
    def remove_readonly(function, path, error):
        entry = Path(path)
        # Windows/OneDrive directories may carry READONLY even when ACLs allow deletion.
        # Only clear that attribute after a permission failure inside this validated tree.
        if (os.name == 'nt' and isinstance(error, PermissionError)
                and entry.resolve().is_relative_to(folder)
                and not entry.is_symlink() and not entry.is_junction()
                and entry.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY):
            entry.chmod(stat.S_IWRITE)
            function(path)
            return
        raise error

    shutil.rmtree(folder, onexc=remove_readonly)
