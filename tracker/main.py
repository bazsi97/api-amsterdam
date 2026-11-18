"""Daily run: read every venue agenda, extract events, update the database and report.

Usage:
    python -m tracker.main                 # normal run
    python -m tracker.main --only Ahoy     # just sources whose name contains "Ahoy"
    python -m tracker.main --fetch-only    # render pages and show text sizes, no API calls
    python -m tracker.main --force         # re-extract even if a page did not change
"""

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

from tracker.extract import ExtractionError, extract_events
from tracker.fetch import render_pages

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "tracker" / "sources.yaml"
DATA = ROOT / "data"
EVENTS_JSON = DATA / "events.json"
PAGES_JSON = DATA / "pages.json"
EVENTS_CSV = DATA / "events.csv"
REPORT_MD = ROOT / "REPORT.md"

# Categories worth pitching photography to. Concerts and theatre are kept in the
# data but left out of the report's main lists.
BUSINESS = ["trade_fair", "conference", "corporate", "consumer_show", "sport", "other"]
LABELS = {
    "trade_fair": "Trade fair", "conference": "Conference", "corporate": "Corporate",
    "consumer_show": "Consumer show", "sport": "Sport", "concert": "Concert",
    "theatre": "Theatre", "other": "Other",
}

# A page shorter than this almost certainly failed to render its calendar.
MIN_TEXT_CHARS = 400


def load_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save_json(path, obj):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def event_id(source_name, ev):
    when = (ev.get("start_date") or ev.get("date_text") or "")[:7]
    return f"{slug(source_name)}/{slug(ev['name'])}/{slug(when)}"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="only sources whose name contains this text")
    ap.add_argument("--fetch-only", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dump", metavar="DIR", help="also save each page's rendered text to DIR")
    args = ap.parse_args(argv)

    sources = yaml.safe_load(SOURCES.read_text())
    if args.only:
        sources = [s for s in sources if args.only.lower() in s["name"].lower()]
    DATA.mkdir(exist_ok=True)
    events = load_json(EVENTS_JSON, {})
    pages = load_json(PAGES_JSON, {})
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date().isoformat()
    tokens = {"input_tokens": 0, "output_tokens": 0}

    url_to_source = {u: s for s in sources for u in s["urls"]}
    for url, final_url, text, error in render_pages(list(url_to_source)):
        source = url_to_source[url]
        h = hashlib.sha256(text.encode()).hexdigest()
        prev = pages.get(url, {})
        rec = {"source": source["name"], "final_url": final_url, "chars": len(text),
               "checked": now.isoformat(timespec="seconds"), "note": None}

        if error or len(text) < MIN_TEXT_CHARS:
            rec.update(status="error", error=error or f"only {len(text)} characters of text rendered")
            pages[url] = {**prev, **rec}
            print(f"[{source['name']}] FAILED: {rec['error']}")
            continue

        if args.dump:
            Path(args.dump).mkdir(parents=True, exist_ok=True)
            (Path(args.dump) / f"{slug(source['name'])}.txt").write_text(text)
        if args.fetch_only:
            print(f"[{source['name']}] {len(text)} chars from {final_url}")
            continue

        if h == prev.get("hash") and prev.get("status") == "ok" and not args.force:
            rec.update(status="ok", hash=h, note="unchanged")
            pages[url] = {**prev, **rec}
            for e in events.values():
                if e["source_url"] == url:
                    e["last_seen"] = today
            print(f"[{source['name']}] unchanged, skipped")
            continue

        try:
            found, usage = extract_events(source, final_url, text, today)
        except ExtractionError as e:
            rec.update(status="error", error=str(e))
            pages[url] = {**prev, **rec}
            print(f"[{source['name']}] EXTRACTION FAILED: {e}")
            continue
        for k in tokens:
            tokens[k] += usage[k]

        new = 0
        for ev in found:
            eid = event_id(source["name"], ev)
            existing = events.get(eid)
            ev.update(
                source=source["name"],
                source_url=url,
                venue=ev.get("venue") or (source["name"] if source["kind"] == "venue" else None),
                city=ev.get("city") or (source["city"] if source["kind"] == "venue" else None),
                first_seen=existing["first_seen"] if existing else today,
                last_seen=today,
            )
            if not existing:
                new += 1
            events[eid] = ev
        rec.update(status="ok", hash=h, error=None, events=len(found), extracted=today)
        pages[url] = {**prev, **rec}
        print(f"[{source['name']}] {len(found)} events ({new} new)")

    if args.fetch_only:
        return 0

    save_json(EVENTS_JSON, events)
    save_json(PAGES_JSON, pages)
    write_csv(events, today)
    write_report(events, pages, today, tokens)
    print(f"Tokens used: {tokens['input_tokens']} in / {tokens['output_tokens']} out")
    return 0


def upcoming(events, today):
    """Events not yet over, sorted by date (undated last)."""
    out = [e for e in events.values() if (e.get("end_date") or e.get("start_date") or "9999") >= today]
    return sorted(out, key=lambda e: (e.get("start_date") or "9999", e["name"].lower()))


def write_csv(events, today):
    cols = ["start_date", "end_date", "date_text", "name", "category", "venue", "city",
            "organiser", "url", "description", "source", "first_seen", "last_seen"]
    with EVENTS_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for e in upcoming(events, today):
            w.writerow(e)


def _row(e):
    name = f"[{e['name']}]({e['url']})" if e.get("url") else e["name"]
    when = e.get("start_date") or e.get("date_text") or "?"
    if e.get("end_date") and e["end_date"] != e.get("start_date"):
        when += f" – {e['end_date']}"
    where = ", ".join(x for x in [e.get("venue"), e.get("city")] if x)
    cells = [when, name, LABELS.get(e["category"], e["category"]), where, e.get("organiser") or ""]
    return "| " + " | ".join(c.replace("|", "/") for c in cells) + " |"


HEADER = "| When | Event | Type | Where | Organiser |\n|---|---|---|---|---|"


def write_report(events, pages, today, tokens):
    up = upcoming(events, today)
    biz = [e for e in up if e["category"] in BUSINESS]
    new = [e for e in biz if e["first_seen"] == today]
    lines = [
        "# Upcoming events at Dutch venues",
        "",
        f"Last updated: **{today}** · {len(biz)} upcoming business/trade events "
        f"({len(up) - len(biz)} concerts and shows hidden; all are in `data/events.csv`).",
        "",
        f"## New since last run ({len(new)})",
        "",
    ]
    lines += [HEADER, *map(_row, new)] if new else ["Nothing new today."]

    lines += ["", "## All upcoming business events", ""]
    by_month = {}
    for e in biz:
        key = (e.get("start_date") or "")[:7] or "Date not announced"
        by_month.setdefault(key, []).append(e)
    for month, evs in by_month.items():
        title = month
        if re.fullmatch(r"\d{4}-\d{2}", month):
            title = dt.date.fromisoformat(month + "-01").strftime("%B %Y")
        lines += [f"### {title}", "", HEADER, *map(_row, evs), ""]

    lines += ["## Source health", "", "| Source | Status | Events | Last checked | Note |", "|---|---|---|---|---|"]
    for url, p in sorted(pages.items(), key=lambda kv: kv[1]["source"]):
        status = "✅" if p.get("status") == "ok" else "❌"
        note = p.get("error") or p.get("note") or ""
        lines.append(f"| [{p['source']}]({url}) | {status} | {p.get('events', '')} | "
                     f"{p.get('checked', '')[:10]} | {note} |")
    REPORT_MD.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
