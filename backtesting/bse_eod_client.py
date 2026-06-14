"""
BSE official FO EOD bhavcopy client — download + parse for SENSEX audit.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path
from typing import List, Optional

import pandas as pd
import requests

from backtesting.nse_eod_client import FoBhavRow

logger = logging.getLogger(__name__)

BSE_HOME = "https://www.bseindia.com"
EOD_CACHE_DIR = Path("data/bse_eod_cache")
UNDERLYINGS = frozenset({"SENSEX"})


class BseEodClient:
    def __init__(self, cache_dir: Path = EOD_CACHE_DIR, timeout: float = 15.0):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout

    def _session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Referer": f"{BSE_HOME}/markets/MarketInfo/BhavCopy.aspx",
        })
        return s

    def _cache_path(self, trade_date: date, *, legacy: bool = False) -> Path:
        suffix = "ms.csv" if legacy else "csv"
        return self.cache_dir / f"BSE_FO_{trade_date.isoformat()}.{suffix}"

    def _udiff_url(self, trade_date: date) -> str:
        ymd = trade_date.strftime("%Y%m%d")
        return (
            f"{BSE_HOME}/download/BhavCopy/Derivative/"
            f"BhavCopy_BSE_FO_0_0_0_{ymd}_F_0000.CSV"
        )

    def _legacy_ms_url(self, trade_date: date) -> str:
        ymd = trade_date.strftime("%Y%m%d")
        return f"{BSE_HOME}/download/Bhavcopy/Derivative/MS_{ymd}-01.csv"

    def download_fo_bhavcopy(self, trade_date: date, *, force_refresh: bool = False) -> Optional[Path]:
        cached = self._cache_path(trade_date)
        if cached.exists() and not force_refresh:
            return cached

        session = self._session()
        for url, legacy in ((self._udiff_url(trade_date), False), (self._legacy_ms_url(trade_date), True)):
            try:
                resp = session.get(url, timeout=self.timeout)
                if resp.status_code != 200 or not resp.content:
                    continue
                out = self._cache_path(trade_date, legacy=legacy)
                out.write_bytes(resp.content)
                time.sleep(0.4)
                return out
            except Exception as exc:
                logger.debug("BSE bhavcopy fetch %s failed: %s", url, exc)
                continue

        logger.warning("Bhavcopy download failed for %s", trade_date)
        return None

    def parse_fo_bhavcopy(self, path: Path) -> pd.DataFrame:
        df = pd.read_csv(path)
        df.columns = [str(c).strip() for c in df.columns]
        return df

    def _rows_from_udiff(self, df: pd.DataFrame, underlying: str) -> List[FoBhavRow]:
        u = underlying.upper()
        sym_col = next((c for c in df.columns if c in ("TckrSymb", "TICKER_SYMBOL")), None)
        if sym_col is None:
            sym_col = next((c for c in df.columns if "SYMBOL" in c.upper()), df.columns[0])
        inst_col = next((c for c in df.columns if c in ("FinInstrmTp", "INSTRUMENT")), None)
        name_col = next((c for c in df.columns if c in ("FinInstrmNm", "SYMBOL_NAME")), None)

        rows: List[FoBhavRow] = []
        for _, row in df.iterrows():
            sym = str(row.get(sym_col, "")).upper()
            if u not in sym:
                continue
            name = str(row.get(name_col, sym)).upper() if name_col else sym
            if "FUT" not in name:
                continue
            if inst_col:
                inst = str(row.get(inst_col, "")).upper()
                if inst and inst not in ("IDF", "FUT", "FUTIDX", "IF"):
                    if "FUT" not in inst:
                        continue

            def _f(key_options: List[str]) -> float:
                for key in key_options:
                    for col in df.columns:
                        if key.upper() in col.upper().replace(" ", ""):
                            try:
                                return float(row[col])
                            except Exception:
                                pass
                return 0.0

            rows.append(FoBhavRow(
                tradingsymbol=name,
                underlying=u,
                open=_f(["OpnPric", "OPEN"]),
                high=_f(["HghPric", "HIGH"]),
                low=_f(["LwPric", "LOW"]),
                close=_f(["ClsPric", "CLOSE", "LAST"]),
                settle=_f(["SttlmPric", "SETTLE", "SETTLEMENT"]),
                volume=int(_f(["TtlTradgVol", "CONTRACTS", "VOLUME", "TOTTRDQTY"])),
                open_interest=int(_f(["OpnIntrst", "OI", "OPEN_INT"])),
            ))
        return rows

    def _rows_from_legacy_ms(self, df: pd.DataFrame, underlying: str) -> List[FoBhavRow]:
        u = underlying.upper()
        sym_col = next((c for c in df.columns if "SERIES" in c.upper() and "CODE" in c.upper()), None)
        if sym_col is None:
            return []
        type_col = next((c for c in df.columns if "PRODUCT TYPE" in c.upper()), None)

        rows: List[FoBhavRow] = []
        for _, row in df.iterrows():
            sym = str(row.get(sym_col, "")).upper()
            if u not in sym or "FUT" not in sym:
                continue
            if type_col and str(row.get(type_col, "")).upper() not in ("IF", "FUT", ""):
                continue

            def _f(key_options: List[str]) -> float:
                for key in key_options:
                    for col in df.columns:
                        if key.upper() in col.upper():
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
                close=_f(["CLOSE"]),
                settle=_f(["CLOSE", "SETTLE"]),
                volume=int(_f(["TOTAL TRADED QUANTITY", "VOLUME", "CONTRACTS"])),
                open_interest=int(_f(["OPEN INTEREST", "OI"])),
            ))
        return rows

    def get_index_futures_rows(self, trade_date: date, underlying: str = "SENSEX") -> List[FoBhavRow]:
        path = self.download_fo_bhavcopy(trade_date)
        if path is None:
            return []
        try:
            df = self.parse_fo_bhavcopy(path)
        except Exception as exc:
            logger.warning("parse BSE bhavcopy failed: %s", exc)
            return []

        if "TckrSymb" in df.columns or "FinInstrmNm" in df.columns:
            return self._rows_from_udiff(df, underlying)
        return self._rows_from_legacy_ms(df, underlying)

    def get_front_month_eod(self, trade_date: date, underlying: str = "SENSEX") -> Optional[FoBhavRow]:
        rows = self.get_index_futures_rows(trade_date, underlying)
        if not rows:
            return None
        return max(rows, key=lambda r: r.open_interest or r.volume)


bse_eod_client = BseEodClient()