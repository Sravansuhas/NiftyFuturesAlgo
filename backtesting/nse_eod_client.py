"""
NSE official FO EOD bhavcopy client — download + parse for audit.
"""

from __future__ import annotations

import io
import json
import logging
import re
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

NSE_HOME = "https://www.nseindia.com"
ARCHIVES = "https://nsearchives.nseindia.com"
EOD_CACHE_DIR = Path("data/nse_eod_cache")
UNDERLYINGS = frozenset({"NIFTY", "BANKNIFTY"})


@dataclass
class FoBhavRow:
    tradingsymbol: str
    underlying: str
    open: float
    high: float
    low: float
    close: float
    settle: float
    volume: int
    open_interest: int
    expiry: Optional[str] = None


class NseEodClient:
    def __init__(self, cache_dir: Path = EOD_CACHE_DIR, timeout: float = 15.0):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout

    def _session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Referer": f"{NSE_HOME}/all-reports-derivatives",
        })
        s.get(NSE_HOME, timeout=self.timeout)
        return s

    def _cache_path(self, trade_date: date, ext: str = "zip") -> Path:
        return self.cache_dir / f"FO_{trade_date.isoformat()}.{ext}"

    def list_merged_daily_reports(self, trade_date: date) -> Dict[str, Any]:
        """NSE merged daily reports index for FO segment."""
        session = self._session()
        label = trade_date.strftime("%d-%b-%Y")
        url = f"{NSE_HOME}/api/merged-daily-reports?key=FO&date={label}&type=equity&mode=single"
        resp = session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def download_fo_bhavcopy(self, trade_date: date, *, force_refresh: bool = False) -> Optional[Path]:
        cached = self._cache_path(trade_date)
        if cached.exists() and not force_refresh:
            return cached

        try:
            meta = self.list_merged_daily_reports(trade_date)
            files = meta.get("files") or meta.get("FO") or []
            download_url = None
            for f in files:
                if not isinstance(f, dict):
                    continue
                key = str(f.get("fileKey") or f.get("key") or "")
                if "BHAVCOPY" in key.upper() or "UDIFF" in key.upper():
                    base = f.get("filePath") or ARCHIVES
                    name = f.get("fileActlName") or f.get("fileName") or ""
                    download_url = f"{base.rstrip('/')}/{name.lstrip('/')}"
                    break
            if not download_url:
                ymd = trade_date.strftime("%Y%m%d")
                download_url = f"{ARCHIVES}/content/fo/BhavCopy_NSE_FO_0_0_0_{ymd}_F_0000.csv.zip"

            session = self._session()
            resp = session.get(download_url, timeout=self.timeout)
            if resp.status_code != 200:
                logger.warning("Bhavcopy download failed %s: %s", trade_date, resp.status_code)
                return None
            cached.write_bytes(resp.content)
            time.sleep(0.4)
            return cached
        except Exception as exc:
            logger.warning("download_fo_bhavcopy %s: %s", trade_date, exc)
            return None

    def parse_fo_bhavcopy(self, path: Path) -> pd.DataFrame:
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as zf:
                name = zf.namelist()[0]
                raw = zf.read(name)
            df = pd.read_csv(io.BytesIO(raw))
        else:
            df = pd.read_csv(path)
        df.columns = [str(c).strip() for c in df.columns]
        return df

    def get_index_futures_rows(self, trade_date: date, underlying: str = "NIFTY") -> List[FoBhavRow]:
        path = self.download_fo_bhavcopy(trade_date)
        if path is None:
            return []
        try:
            df = self.parse_fo_bhavcopy(path)
        except Exception as exc:
            logger.warning("parse bhavcopy failed: %s", exc)
            return []

        sym_col = next((c for c in df.columns if "SYMBOL" in c.upper() or "Tckr" in c), None)
        if sym_col is None:
            sym_col = df.columns[0]
        inst_col = next((c for c in df.columns if "INSTRUMENT" in c.upper()), None)

        rows: List[FoBhavRow] = []
        u = underlying.upper()
        for _, row in df.iterrows():
            sym = str(row.get(sym_col, "")).upper()
            if u not in sym or "FUT" not in sym:
                continue
            if inst_col and str(row.get(inst_col, "")).upper() not in ("", "FUT", "FUTIDX"):
                if "FUT" not in str(row.get(inst_col, "")).upper():
                    continue

            def _f(key_options: List[str]) -> float:
                for k in key_options:
                    for col in df.columns:
                        if k.upper() in col.upper():
                            try:
                                return float(row[col])
                            except Exception:
                                pass
                return 0.0

            rows.append(FoBhavRow(
                tradingsymbol=sym,
                underlying=u,
                open=_f(["OPEN"]),
                high=_f(["HIGH"]),
                low=_f(["LOW"]),
                close=_f(["CLOSE", "LTP"]),
                settle=_f(["SETTLE", "SETTLEMENT"]),
                volume=int(_f(["CONTRACTS", "VOLUME", "TOTTRDQTY"])),
                open_interest=int(_f(["OI", "OPEN_INT"])),
            ))
        return rows

    def get_front_month_eod(self, trade_date: date, underlying: str = "NIFTY") -> Optional[FoBhavRow]:
        rows = self.get_index_futures_rows(trade_date, underlying)
        if not rows:
            return None
        return max(rows, key=lambda r: r.open_interest or r.volume)


nse_eod_client = NseEodClient()