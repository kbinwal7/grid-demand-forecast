#!/usr/bin/env python3
"""
Grid-India daily PSP report -> 15-min (96 block) time-series CSV.

Standalone: only needs  pip install requests pandas openpyxl xlrd

CLI
---
  # date range (inclusive)
  python psp_scraper.py extract --start 2024-11-01 --end 2026-10-31

  # last N days ending yesterday
  python psp_scraper.py extract --last-days 730

  # parse Excel files you downloaded by hand (no network)
  python psp_scraper.py parse-dir ./my_downloads

  # debug: check the listing API + download for one date
  python psp_scraper.py probe 2026-10-31

Use from your project
---------------------
  from psp_scraper import extract_range, parse_psp_file, Sink
  df = extract_range(date(2025, 1, 1), date(2025, 1, 31))   # DataFrame
  # DB later: subclass Sink (see DbSink example at the bottom) and pass sink=...

Output schema (one row per 15-min block, unique key = date + block)
-------------------------------------------------------------------
  date, block (1..96), time (HH:MM), timestamp, frequency_hz, demand_met_mw,
  storage_demand_mw, nuclear_mw, wind_mw, solar_mw, hydro_mw, gas_mw,
  thermal_mw, storage_psp_bess_mw, others_mw, net_demand_met_mw,
  total_generation_mw, net_transnational_exchange_mw, source_file
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
import requests

log = logging.getLogger("psp")

# --------------------------------------------------------------------------
# CONFIG - the only part you should ever need to edit
# --------------------------------------------------------------------------
# Grid-India publishes a JSON listing API + a CDN for the files (the old
# report.grid-india.in host no longer exists). Verified against a public
# project that uses the same endpoints; if the site changes again, this is
# the only block to edit.
API_URL = "https://webapi.grid-india.in/api/v1/file"
CDN_BASE = "https://webcdn.grid-india.in/"
API_SOURCE = "GRDW"
API_TYPE = "DAILY_PSP_REPORT"
SHEET_HINT = "timeseries"           # sheet whose name contains this (case-insens.)
RAW_DIR_DEFAULT = "data/psp_raw"
OUT_CSV_DEFAULT = "data/psp_timeseries.csv"
REQUEST_DELAY_S = 0.3               # politeness delay per worker
HEADERS = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}

KEY_COLS = ["date", "block"]
VALUE_COLS = [
    "frequency_hz", "demand_met_mw", "storage_demand_mw", "nuclear_mw",
    "wind_mw", "solar_mw", "hydro_mw", "gas_mw", "thermal_mw",
    "storage_psp_bess_mw", "others_mw", "net_demand_met_mw",
    "total_generation_mw", "net_transnational_exchange_mw",
]
OUT_COLS = KEY_COLS + ["time", "timestamp"] + VALUE_COLS + ["source_file"]

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def _canon(header: object) -> Optional[str]:
    """Map a header cell like 'DEMAND MET¹\\n(MW)' to a canonical column name."""
    if header is None or (isinstance(header, float) and pd.isna(header)):
        return None
    h = re.sub(r"[^a-z& ]", " ", str(header).lower())
    h = re.sub(r"\s+", " ", h).strip()
    if not h:
        return None
    if "freq" in h:
        return "frequency_hz"
    if h.startswith("time"):
        return "time"
    if "transnational" in h or "exchange" in h:
        return "net_transnational_exchange_mw"
    if "storage" in h and "demand" in h:
        return "storage_demand_mw"
    if "net" in h and "demand" in h:
        return "net_demand_met_mw"
    if "demand" in h:
        return "demand_met_mw"
    if "total" in h and "gener" in h:
        return "total_generation_mw"
    if "storage" in h:
        return "storage_psp_bess_mw"
    for k in ("nuclear", "wind", "solar", "hydro", "gas", "thermal", "others"):
        if k in h:
            return f"{k}_mw"
    return None


def _minutes(v: object) -> Optional[int]:
    """Cell -> minutes since midnight. Handles time, datetime, 'H:MM', day-fraction."""
    if isinstance(v, (dt.time, dt.datetime, pd.Timestamp)):
        return v.hour * 60 + v.minute
    if isinstance(v, (int, float)) and not pd.isna(v) and 0 <= v < 1:
        return int(round(v * 1440))
    if isinstance(v, str):
        m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*", v)
        if m and int(m[1]) < 24 and int(m[2]) < 60:
            return int(m[1]) * 60 + int(m[2])
    return None


def date_from_filename(path: Path) -> dt.date:
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{2})", path.name)
    if not m:
        raise ValueError(f"Cannot read date from file name: {path.name} (expected DD.MM.YY_...)")
    return dt.date(2000 + int(m[3]), int(m[2]), int(m[1]))


def _open_excel(path: Path) -> pd.ExcelFile:
    """Open xls/xlsx choosing the engine from the file's real content.

    pandas' auto-pick differs between versions (new ones may want python-calamine),
    so we try the right engine first, then fall back to the others.
    """
    head = path.read_bytes()[:8]
    if head[:2] == b"PK":
        order = ["openpyxl", "calamine"]
    elif head[:4] == b"\xd0\xcf\x11\xe0":
        order = ["xlrd", "calamine"]
    else:
        raise ValueError(f"{path.name} is not an Excel file (starts with {head!r})")
    errors = []
    for eng in order:
        try:
            return pd.ExcelFile(path, engine=eng)
        except ImportError as e:
            errors.append(f"{eng}: not installed")
        except Exception as e:
            errors.append(f"{eng}: {e}")
    raise RuntimeError(f"Cannot read {path.name}; tried " + "; ".join(errors)
                       + "  ->  pip install openpyxl xlrd")


def parse_psp_file(path: str | Path, day: Optional[dt.date] = None) -> pd.DataFrame:
    """Parse the TimeSeries sheet of one PSP Excel file -> DataFrame (<=96 rows)."""
    path = Path(path)
    day = day or date_from_filename(path)

    xl = _open_excel(path)
    sheet = next((s for s in xl.sheet_names if SHEET_HINT in s.lower().replace(" ", "")), None)
    if sheet is None:
        raise ValueError(f"No '{SHEET_HINT}' sheet in {path.name}; sheets={xl.sheet_names}")
    raw = xl.parse(sheet, header=None, dtype=object)

    # header row = row with a cell starting "TIME" AND a cell containing "FREQ"
    # (the title row also says "FREQUENCY", so FREQ alone is not enough)
    def _is_header(row) -> bool:
        cells = [str(c).strip().upper() for c in row if isinstance(c, str)]
        return any(c.startswith("TIME") for c in cells) and any("FREQ" in c for c in cells)

    hdr_idx = next((i for i in range(min(40, len(raw))) if _is_header(raw.iloc[i])), None)
    if hdr_idx is None:
        raise ValueError(f"Header row not found in {path.name}")
    names = [_canon(c) for c in raw.iloc[hdr_idx]]

    cols, seen = {}, set()
    for i, n in enumerate(names):
        if n and n not in seen:
            cols[i] = n
            seen.add(n)
    if "time" not in seen:  # fall back: time is the first column
        cols[0] = "time"

    body = raw.iloc[hdr_idx + 1:, list(cols)].rename(columns=cols)
    mins = body["time"].map(_minutes)
    body = body[mins.notna()].copy()
    body["_m"] = mins[mins.notna()].astype(int)
    body = body[body["_m"] % 15 == 0].drop_duplicates("_m").sort_values("_m")

    df = pd.DataFrame({
        "date": day.isoformat(),
        "block": body["_m"] // 15 + 1,
        "time": body["_m"].map(lambda m: f"{m // 60:02d}:{m % 60:02d}"),
    })
    df["timestamp"] = pd.to_datetime(df["date"]) + pd.to_timedelta(body["_m"], unit="m")
    for c in VALUE_COLS:
        df[c] = pd.to_numeric(body[c], errors="coerce") if c in body else float("nan")
    df["source_file"] = path.name

    if len(df) != 96:
        log.warning("%s: expected 96 blocks, got %d", path.name, len(df))
    return df[OUT_COLS].reset_index(drop=True)


# --------------------------------------------------------------------------
# Sinks (CSV now, DB later) - extraction code never needs to change
# --------------------------------------------------------------------------
class Sink:
    """Implement write(df). `df` always has OUT_COLS; (date, block) is the unique key."""
    def write(self, df: pd.DataFrame) -> None:  # pragma: no cover
        raise NotImplementedError


class CsvSink(Sink):
    """Idempotent: merges with the existing CSV, replacing rows with the same key."""
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def write(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            old = pd.read_csv(self.path, dtype={"date": str})
            df = pd.concat([old, df.assign(timestamp=df["timestamp"].astype(str))], ignore_index=True)
        df = (df.drop_duplicates(KEY_COLS, keep="last")
                .sort_values(KEY_COLS).reset_index(drop=True))
        tmp = self.path.with_suffix(".tmp")
        df.to_csv(tmp, index=False)
        os.replace(tmp, self.path)  # atomic


# Example for later - uncomment and pass sink=DbSink("postgresql://...")
# class DbSink(Sink):
#     def __init__(self, url, table="psp_timeseries"):
#         from sqlalchemy import create_engine
#         self.engine, self.table = create_engine(url), table
#     def write(self, df):
#         # simplest idempotent approach: delete the dates, then insert
#         from sqlalchemy import text
#         dates = df["date"].unique().tolist()
#         with self.engine.begin() as cx:
#             cx.execute(text(f"DELETE FROM {self.table} WHERE date IN :d").bindparams(
#                 __import__("sqlalchemy").bindparam("d", expanding=True)), {"d": dates})
#             df.to_sql(self.table, cx, if_exists="append", index=False)


# --------------------------------------------------------------------------
# Downloading
# --------------------------------------------------------------------------
_TITLE_RE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{2})")


def _fy(year: int, month: int) -> str:
    """Indian financial year label used by the API, e.g. 2025-26."""
    return f"{year}-{str(year + 1)[-2:]}" if month >= 4 else f"{year - 1}-{str(year)[-2:]}"


def _title_date(title: str) -> Optional[dt.date]:
    m = _TITLE_RE.match(title or "")
    if not m:
        return None
    try:
        return dt.date(2000 + int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None


def _revision(title: str) -> int:
    """'09.06.26_NLDC_PSP_128' -> 128 ; no suffix -> -1 (higher = newer revision)."""
    m = re.search(r"_(\d+)$", re.sub(r"\.(xlsx|xls)$", "", title or "", flags=re.I))
    return int(m[1]) if m else -1


def _is_excel(item: dict) -> bool:
    mime = str(item.get("MimeType", "")).lower()
    path = str(item.get("FilePath", "")).lower()
    return any(k in mime for k in ("excel", "spreadsheet", "xls")) or path.endswith((".xls", ".xlsx"))


def _ext_from_magic(b: bytes) -> Optional[str]:
    if b[:2] == b"PK":
        return ".xlsx"
    if b[:4] == b"\xd0\xcf\x11\xe0":
        return ".xls"
    return None  # HTML error page, PDF, etc.


def _cached(raw_dir: Path, d: dt.date) -> Optional[Path]:
    prefix = f"{d.day:02d}.{d.month:02d}.{d.year % 100:02d}_"
    hits = [p for p in raw_dir.glob(prefix + "*") if p.suffix.lower() in (".xls", ".xlsx")
            and p.stat().st_size > 1000]
    return max(hits, key=lambda p: _revision(p.stem)) if hits else None


class GridIndiaClient:
    """Thin wrapper around the Grid-India file-listing API + CDN."""

    def __init__(self, verify: bool = True):
        self.verify = verify
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._listings: dict[tuple[int, int], list[dict]] = {}
        if not verify:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def listing(self, year: int, month: int) -> list[dict]:
        """All PSP files the portal lists under (year, month). Cached."""
        key = (year, month)
        if key in self._listings:
            return self._listings[key]
        payload = {"_source": API_SOURCE, "_type": API_TYPE,
                   "_fileDate": _fy(year, month), "_month": f"{month:02d}"}
        last = None
        for attempt in range(3):
            try:
                r = self.session.post(API_URL, json=payload, timeout=(10, 60), verify=self.verify)
                r.raise_for_status()
                data = r.json()
                break
            except (requests.RequestException, ValueError) as e:
                last = e
                log.debug("listing %s attempt %d: %s", key, attempt + 1, e)
                time.sleep(2 ** attempt)
        else:
            hint = " (try --insecure)" if "SSL" in str(last) else ""
            raise RuntimeError(f"Listing API failed for {year}-{month:02d}: {last}{hint}")
        items = data.get("retData") if isinstance(data, dict) else data
        self._listings[key] = items or []
        log.debug("listing %s -> %d records", key, len(self._listings[key]))
        return self._listings[key]

    def index(self, start: dt.date, end: dt.date) -> dict[dt.date, dict]:
        """Map data-date -> API record (newest revision wins) for [start, end].

        A day's report is published the next morning, so the last day of a month
        is listed under the *next* month -> we query through end-month + 1.
        """
        y, m = start.year, start.month
        last_y, last_m = (end.year + (end.month == 12), end.month % 12 + 1)
        best: dict[dt.date, dict] = {}
        while (y, m) <= (last_y, last_m):
            for it in self.listing(y, m):
                d = _title_date(it.get("Title_", ""))
                if d and start <= d <= end and _is_excel(it):
                    if d not in best or _revision(it["Title_"]) > _revision(best[d]["Title_"]):
                        best[d] = it
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return best

    @staticmethod
    def url_of(item: dict) -> str:
        return CDN_BASE.rstrip("/") + "/" + str(item["FilePath"]).lstrip("/")

    def download(self, item: dict, raw_dir: Path, retries: int = 3) -> Optional[Path]:
        url = self.url_of(item)
        for attempt in range(retries):
            try:
                r = self.session.get(url, timeout=(10, 120), verify=self.verify,
                                     headers={"User-Agent": HEADERS["User-Agent"]})
                log.debug("%s -> HTTP %s, %d bytes", url, r.status_code, len(r.content))
                time.sleep(REQUEST_DELAY_S)
                ext = _ext_from_magic(r.content[:8]) if r.status_code == 200 else None
                if ext:
                    name = re.sub(r"[\\/*?:\"<>|]", "_", item["Title_"]).strip()
                    name = re.sub(r"\.(xlsx|xls)$", "", name, flags=re.I)
                    out = raw_dir / f"{name}{ext}"
                    out.write_bytes(r.content)
                    return out
                if r.status_code in (403, 404):
                    return None
            except requests.RequestException as e:
                log.debug("%s attempt %d: %s", url, attempt + 1, e)
                time.sleep(2 ** attempt)
        return None


def _daterange(a: dt.date, b: dt.date):
    for i in range((b - a).days + 1):
        yield a + dt.timedelta(days=i)


def extract_range(start: dt.date, end: dt.date, *, raw_dir=RAW_DIR_DEFAULT,
                  sink: Optional[Sink] = None, workers: int = 4,
                  verify=True, refresh=False) -> pd.DataFrame:
    """Download + parse every day in [start, end]. Writes to `sink` if given."""
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    client = GridIndiaClient(verify=verify)

    log.info("Fetching file listings from the Grid-India API ...")
    index = client.index(start, end)
    days = list(_daterange(start, end))
    log.info("Portal lists %d of %d requested days", len(index), len(days))

    def fetch(d):
        if not refresh and (p := _cached(raw_dir, d)):
            return d, p
        item = index.get(d)
        return d, (client.download(item, raw_dir) if item else None)

    frames, missing, failed = [], [], []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for n, (d, path) in enumerate(ex.map(fetch, days), 1):
            if path is None:
                missing.append(d)
                log.info("%s  not published / download failed", d)
            else:
                try:
                    frames.append(parse_psp_file(path, d))
                    log.info("%s  ok  (%s)", d, path.name)
                except Exception as e:  # keep going; report at the end
                    failed.append((d, str(e)))
                    log.error("%s  parse failed: %s", d, e)
            if n == 10 and not frames and not failed and index:
                log.error("First 10 days all failed to download - aborting. "
                          "Run `probe <date>`; try --insecure.")
                ex.shutdown(wait=False, cancel_futures=True)
                break

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=OUT_COLS)
    if sink is not None:
        sink.write(df)
    log.info("done: ok=%d missing=%d parse_failed=%d", len(frames), len(missing), len(failed))
    df.attrs["missing"], df.attrs["failed"] = missing, failed
    return df


def parse_dir(directory: str | Path, sink: Optional[Sink] = None) -> pd.DataFrame:
    frames = []
    for p in sorted(Path(directory).glob("*_NLDC_PSP*.xls*")):
        try:
            frames.append(parse_psp_file(p))
        except Exception as e:
            log.error("skip %s: %s", p.name, e)
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=OUT_COLS)
    if sink is not None:
        sink.write(df)
    return df


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Grid-India daily PSP -> 15-min CSV")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="download + parse a date range")
    e.add_argument("--start", type=_d, help="YYYY-MM-DD")
    e.add_argument("--end", type=_d, help="YYYY-MM-DD (default: yesterday)")
    e.add_argument("--last-days", type=int, help="alternative to --start/--end")
    e.add_argument("--out", default=OUT_CSV_DEFAULT)
    e.add_argument("--raw-dir", default=RAW_DIR_DEFAULT)
    e.add_argument("--workers", type=int, default=4)
    e.add_argument("--insecure", action="store_true", help="skip TLS verification")
    e.add_argument("--refresh", action="store_true", help="re-download cached files")

    p = sub.add_parser("parse-dir", help="parse already-downloaded Excel files")
    p.add_argument("directory")
    p.add_argument("--out", default=OUT_CSV_DEFAULT)

    q = sub.add_parser("probe", help="show URLs tried for one date")
    q.add_argument("date", type=_d)
    q.add_argument("--insecure", action="store_true")

    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    if a.cmd == "extract":
        end = a.end or dt.date.today() - dt.timedelta(days=1)
        start = a.start or (end - dt.timedelta(days=a.last_days - 1) if a.last_days else None)
        if start is None:
            ap.error("give --start or --last-days")
        if start > end:
            ap.error("--start is after --end")
        df = extract_range(start, end, raw_dir=a.raw_dir, sink=CsvSink(a.out),
                           workers=a.workers,
                           verify=not a.insecure, refresh=a.refresh)
        log.info("wrote %d new/updated rows -> %s", len(df), a.out)
        miss = df.attrs.get("missing", [])
        if miss:
            Path(a.out).with_suffix(".missing.txt").write_text(
                "\n".join(d.isoformat() for d in miss))
        return 0 if len(df) else 1

    if a.cmd == "parse-dir":
        df = parse_dir(a.directory, CsvSink(a.out))
        log.info("wrote %d rows -> %s", len(df), a.out)
        return 0 if len(df) else 1

    if a.cmd == "probe":
        c = GridIndiaClient(verify=not a.insecure)
        try:
            idx = c.index(a.date, a.date)
        except RuntimeError as ex:
            print(f"ERR  {ex}")
            return 1
        print(f"Listing API OK. Records under {a.date:%Y-%m}: {len(c.listing(a.date.year, a.date.month))}")
        item = idx.get(a.date)
        if not item:
            print(f"No Excel file listed for {a.date} (not published yet, or title differs).")
            for it in c.listing(a.date.year, a.date.month)[:5]:
                print("  sample record:", it)
            return 1
        url = c.url_of(item)
        r = c.session.get(url, timeout=60, verify=not a.insecure)
        kind = _ext_from_magic(r.content[:8]) or "not an Excel file"
        print(f"{item.get('Title_')}  ->  HTTP {r.status_code}, {len(r.content)} B, {kind}\n{url}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())