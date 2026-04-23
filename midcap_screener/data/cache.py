"""
Parquet cache. OHLCV data doesn't change for past bars, so cache aggressively
and only re-fetch the last ~5 sessions each daily run.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class ParquetCache:
    def __init__(self, root: str | Path = ".cache") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Ticker like RELIANCE.NS -> RELIANCE_NS.parquet
        safe = key.replace(".", "_").replace("/", "_")
        return self.root / f"{safe}.parquet"

    def get(self, key: str) -> Optional[pd.DataFrame]:
        p = self._path(key)
        if not p.exists():
            return None
        try:
            return pd.read_parquet(p)
        except Exception as e:
            logger.warning("Failed to read %s: %s", p, e)
            return None

    def put(self, key: str, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        try:
            df.to_parquet(self._path(key))
        except Exception as e:
            logger.warning("Failed to write cache for %s: %s", key, e)

    def put_many(self, data: Dict[str, pd.DataFrame]) -> None:
        for k, v in data.items():
            self.put(k, v)

    def stamp_file(self) -> Path:
        return self.root / "_last_refresh.txt"

    def last_refresh(self) -> Optional[date]:
        p = self.stamp_file()
        if not p.exists():
            return None
        try:
            return date.fromisoformat(p.read_text().strip())
        except Exception:
            return None

    def stamp_today(self) -> None:
        self.stamp_file().write_text(date.today().isoformat())
