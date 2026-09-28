"""Commercial as-run log for RadioCastOS.

Every commercial spot that actually starts playing is appended to a CSV file
(date, time, campaign, spot, program, file). The log can be filtered by date
range and campaign, exported to CSV, and turned into a printable HTML report.

The module has no UI dependencies so it can be tested on its own.
"""
from __future__ import annotations

import csv
import datetime as dt
import html
import os
import threading
from pathlib import Path
from typing import Iterable, Optional

from scheduler import data_dir

LOG_PATH = data_dir() / "commercial_log.csv"
REPORT_DIR = data_dir() / "reports"
FIELDS = ["date", "time", "campaign", "spot", "program", "file"]
NO_CAMPAIGN = "(no campaign)"

_lock = threading.Lock()


def campaign_for_path(campaigns: Iterable, path: str) -> str:
    """Name of the campaign that owns this spot file, or NO_CAMPAIGN."""
    target = os.path.normcase(os.path.normpath(path))
    for c in campaigns:
        for spot in getattr(c, "spots", []):
            if spot and os.path.normcase(os.path.normpath(spot)) == target:
                return c.name
    return NO_CAMPAIGN


def log_play(campaign: str, path: str, program: str,
             when: Optional[dt.datetime] = None,
             log_path: Optional[Path] = None) -> None:
    """Append one play to the as-run log."""
    when = when or dt.datetime.now()
    target = Path(log_path) if log_path else LOG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "date": when.strftime("%Y-%m-%d"),
        "time": when.strftime("%H:%M:%S"),
        "campaign": campaign or NO_CAMPAIGN,
        "spot": Path(path).stem,
        "program": program,
        "file": path,
    }
    with _lock:
        is_new = not target.exists() or target.stat().st_size == 0
        # BOM only on a brand-new file so Excel reads it correctly.
        with target.open("a", newline="", encoding="utf-8-sig" if is_new else "utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            if is_new:
                writer.writeheader()
            writer.writerow(row)


def read_log(start: Optional[dt.date] = None, end: Optional[dt.date] = None,
             campaign: str = "", log_path: Optional[Path] = None) -> list[dict]:
    """Return log rows in date/time order, filtered by inclusive date range and campaign."""
    source = Path(log_path) if log_path else LOG_PATH
    if not source.exists():
        return []
    rows: list[dict] = []
    with _lock:
        with source.open("r", newline="", encoding="utf-8-sig") as f:
            for raw in csv.DictReader(f):
                try:
                    day = dt.date.fromisoformat((raw.get("date") or "").strip())
                except ValueError:
                    continue
                if start and day < start:
                    continue
                if end and day > end:
                    continue
                if campaign and (raw.get("campaign") or "") != campaign:
                    continue
                rows.append({k: (raw.get(k) or "") for k in FIELDS})
    rows.sort(key=lambda r: (r["date"], r["time"]))
    return rows


def campaign_names(log_path: Optional[Path] = None) -> list[str]:
    return sorted({r["campaign"] for r in read_log(log_path=log_path)})


def summarize(rows: list[dict]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["campaign"]] = counts.get(r["campaign"], 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))


def export_csv(rows: list[dict], dest: Path) -> None:
    with Path(dest).open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def report_html(rows: list[dict], station: str, start: Optional[dt.date],
                end: Optional[dt.date], campaign: str = "",
                auto_print: bool = True) -> str:
    e = html.escape
    period = f"{start.isoformat() if start else 'start'} to {end.isoformat() if end else 'today'}"
    generated = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    summary = "".join(
        f"<tr><td>{e(name)}</td><td class='n'>{count}</td></tr>" for name, count in summarize(rows)
    ) or "<tr><td colspan='2'>No plays in this period</td></tr>"
    detail = "".join(
        f"<tr><td class='n'>{i}</td><td>{e(r['date'])}</td><td>{e(r['time'])}</td>"
        f"<td>{e(r['campaign'])}</td><td>{e(r['spot'])}</td><td>{e(r['program'])}</td></tr>"
        for i, r in enumerate(rows, 1)
    )
    script = ("<script>window.addEventListener('load',function(){setTimeout(function(){window.print();},400);});</script>"
              if auto_print else "")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Commercial as-run report - {e(station)}</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;color:#111;margin:24px;font-size:13px}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:15px;margin:22px 0 6px}}
.meta{{color:#444;margin-bottom:10px}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #999;padding:5px 8px;text-align:left}}
th{{background:#eee}} td.n{{text-align:right;width:70px}}
.sign{{margin-top:36px;display:flex;gap:40px}}
.sign div{{flex:1;border-top:1px solid #333;padding-top:4px;color:#444}}
tr{{page-break-inside:avoid}} thead{{display:table-header-group}}
@media print{{body{{margin:12mm}}}}
</style></head><body>
<h1>Commercial as-run report</h1>
<div class="meta"><b>{e(station)}</b><br>Period: {e(period)}<br>
Campaign: {e(campaign) if campaign else 'All campaigns'}<br>
Total plays: <b>{len(rows)}</b> &nbsp;|&nbsp; Generated: {e(generated)}</div>
<h2>Summary by campaign</h2>
<table><thead><tr><th>Campaign</th><th>Plays</th></tr></thead><tbody>{summary}</tbody></table>
<h2>Play log</h2>
<table><thead><tr><th>#</th><th>Date</th><th>Time</th><th>Campaign</th><th>Spot</th><th>Program</th></tr></thead>
<tbody>{detail}</tbody></table>
<div class="sign"><div>Prepared by</div><div>Checked by</div><div>Date</div></div>
{script}
</body></html>"""


def write_report(rows: list[dict], station: str, start: Optional[dt.date],
                 end: Optional[dt.date], campaign: str = "",
                 auto_print: bool = True, folder: Optional[Path] = None) -> Path:
    folder = Path(folder) if folder else REPORT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"commercial-asrun-{dt.datetime.now():%Y%m%d-%H%M%S}.html"
    path.write_text(report_html(rows, station, start, end, campaign, auto_print), encoding="utf-8")
    return path
