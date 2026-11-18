# Dutch venue event tracker

Watches the event calendars of the big Dutch conference and exhibition venues and
keeps a list of upcoming trade fairs, conferences and corporate events, so Aaron
can contact organisers about photography well in advance.

**This is the quick first version.** It finds events and organisers. It does not
find contact people or send messages yet.

## Where to look

- **[REPORT.md](REPORT.md)**: what's new since the last run, then everything upcoming by month.
  Concerts and theatre are hidden from the report.
- **[data/events.csv](data/events.csv)**: every upcoming event, including concerts and shows.
  Opens in Excel or Google Sheets.

It updates itself every morning via GitHub Actions. To run it straight away, go to
**Actions → Track venue events → Run workflow**.

## How it works

1. `tracker/sources.yaml` lists the venues and their agenda pages.
2. Each page is opened in a headless browser, because most calendars are built with
   JavaScript. The script scrolls and presses "show more" buttons before reading the text.
3. If a page's text changed since the last run, Claude turns it into a list of events
   (name, dates, type, organiser, link). Pages that haven't changed are skipped, so most
   days cost almost nothing.
4. Events are merged into `data/events.json`, which records when each was first seen,
   and the CSV and report are rebuilt.

## Setup (once)

Add an Anthropic API key as a repository secret named `ANTHROPIC_API_KEY`:
**Settings → Secrets and variables → Actions → New repository secret**.

## Adding a venue

Add an entry to `tracker/sources.yaml` with its name, city and agenda URL. That's all.

## Running locally

```bash
pip install -r requirements.txt
python -m playwright install chromium
export ANTHROPIC_API_KEY=...
python -m tracker.main                       # full run
python -m tracker.main --only Ahoy           # one venue
python -m tracker.main --fetch-only --dump /tmp/pages   # check page rendering, no API calls
```

Optional settings: `TRACKER_MODEL` (default `claude-opus-5-5`) and `TRACKER_EFFORT`
(default `low`).

## Known gaps

- **MECC Maastricht**: the calendar widget renders empty, so no events come through yet.
- **Expo Haarlemmermeer**: the site's SSL certificate had expired when this was built.
- **RAI, MartiniPlaza**: only the events shown on the first screen may be captured.
- Duplicates between a venue's own page and the EventsEye directory aren't merged yet.
- Check the "Source health" table at the bottom of the report to see which venues worked.
