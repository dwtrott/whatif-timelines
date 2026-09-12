"""JSON-on-disk persistence with an in-memory cache. One file per scenario."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .models import Scenario


class Store:
    def __init__(self, data_dir: str):
        self.dir = Path(data_dir) / "scenarios"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Scenario] = {}
        self._lock = threading.RLock()
        self._load_all()

    def _path(self, sid: str) -> Path:
        return self.dir / f"{sid}.json"

    def _load_all(self):
        for p in sorted(self.dir.glob("*.json")):
            try:
                with p.open() as f:
                    sc = Scenario.from_dict(json.load(f))
                # anything that was mid-run when the process died is no longer running
                for b in sc.branches.values():
                    if b.status in ("running", "retrieving"):
                        b.status = "stopped"
                        b.error = b.error or "interrupted (server restarted)"
                if sc.status == "retrieving":
                    sc.status = "failed"
                    sc.error = sc.error or "interrupted (server restarted)"
                self._cache[sc.id] = sc
            except Exception as e:  # noqa: BLE001
                print(f"[store] skipping {p.name}: {e}")

    def list(self) -> list[Scenario]:
        with self._lock:
            return sorted(self._cache.values(), key=lambda s: s.created_at, reverse=True)

    def get(self, sid: str) -> Scenario | None:
        with self._lock:
            return self._cache.get(sid)

    def save(self, sc: Scenario):
        with self._lock:
            self._cache[sc.id] = sc
            tmp = self._path(sc.id).with_suffix(".tmp")
            with tmp.open("w") as f:
                json.dump(sc.to_dict(), f, ensure_ascii=False, indent=1, default=str)
            os.replace(tmp, self._path(sc.id))

    def delete(self, sid: str) -> bool:
        with self._lock:
            sc = self._cache.pop(sid, None)
            p = self._path(sid)
            if p.exists():
                p.unlink()
            return sc is not None
