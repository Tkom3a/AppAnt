from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any

from models import AppState, EventItem, MinerSnapshot


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self.state = AppState()
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.state = AppState.model_validate(raw)
        except (OSError, json.JSONDecodeError, ValueError):
            self.state = AppState()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            self.state.model_dump_json(indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def update(self, **changes: Any) -> AppState:
        with self._lock:
            data = self.state.model_dump()
            data.update(changes)
            self.state = AppState.model_validate(data)
            self.save()
            return self.state

    def put_snapshot(self, snapshot: MinerSnapshot) -> None:
        with self._lock:
            self.state.snapshots[snapshot.id] = snapshot
            self.save()

    def get_snapshot(self, miner_id: str) -> MinerSnapshot | None:
        return self.state.snapshots.get(miner_id)

    def add_event(self, event: EventItem, keep: int = 200) -> None:
        with self._lock:
            events = list(self.state.events)
            events.insert(0, event)
            self.state.events = events[:keep]
            self.save()

    def add_expo_token(self, token: str) -> None:
        token = token.strip()
        if not token:
            return
        with self._lock:
            if token not in self.state.expo_tokens:
                self.state.expo_tokens.append(token)
                self.save()

    def add_reboot_time(self, miner_id: str, ts: str, window: int) -> int:
        from datetime import datetime, timedelta, timezone

        with self._lock:
            times = list(self.state.reboot_times.get(miner_id, []))
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=window)
            kept: list[str] = []
            for item in times:
                try:
                    when = datetime.fromisoformat(item)
                    if when >= cutoff:
                        kept.append(item)
                except ValueError:
                    continue
            kept.append(ts)
            self.state.reboot_times[miner_id] = kept
            self.save()
            return len(kept)

    def set_sleep_applied(self, miner_id: str, sleeping: bool) -> None:
        with self._lock:
            applied = dict(self.state.sleep_applied)
            applied[miner_id] = sleeping
            self.state.sleep_applied = applied
            self.save()
