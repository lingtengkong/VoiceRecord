from __future__ import annotations

import json
import os
import re
import uuid
import time
import tempfile
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
