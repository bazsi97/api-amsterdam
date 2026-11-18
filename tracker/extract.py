"""Turn a venue's agenda text into structured events using Claude."""

import json
import os

import anthropic

MODEL = os.environ.get("TRACKER_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("TRACKER_EFFORT", "low")

CATEGORIES = [
    "trade_fair",      # beurs / vakbeurs / expo aimed at businesses
    "conference",      # conference, congress, summit, symposium, seminar
    "corporate",       # company event, awards, gala, kick-off, shareholder meeting
    "consumer_show",   # public fair / hobby show / market (e.g. bike show, flea market)
    "sport",           # sports or extreme-sports event
    "concert",         # music concert / festival
    "theatre",         # musical, show, comedy, family entertainment
    "other",
]

EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "start_date": {
                        "type": ["string", "null"],
                        "description": "YYYY-MM-DD, or null if no day is given",
                    },
                    "end_date": {"type": ["string", "null"], "description": "YYYY-MM-DD or null"},
                    "date_text": {"type": "string", "description": "Date exactly as written on the page"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "organiser": {"type": ["string", "null"]},
                    "venue": {
                        "type": ["string", "null"],
                        "description": "Venue/hall name if stated (important for directory pages)",
                    },
                    "city": {"type": ["string", "null"]},
                    "url": {"type": ["string", "null"], "description": "Link to the event page, if present"},
                    "description": {"type": ["string", "null"], "description": "One short sentence, English"},
                },
                "required": [
                    "name", "start_date", "end_date", "date_text", "category",
                    "organiser", "venue", "city", "url", "description",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["events"],
    "additionalProperties": False,
}

SYSTEM = """You extract event listings from the text of Dutch venue and trade-fair websites.
The text was scraped from a rendered web page; links appear as "text <url>".

Return every distinct upcoming event that is listed. Rules:
- One entry per event, even if the page lists it once per day; give the full date range.
- Dates: convert to YYYY-MM-DD. If the year is missing, pick the next occurrence on or after
  today's date. If only a month is known, set start_date to null and keep date_text.
- Ignore navigation, cookie banners, newsletter sign-ups, room-hire pages and past events.
- Do not invent organisers, dates or URLs: use null when the page does not say.
- Categories: trade_fair (business-to-business beurs/vakbeurs), conference (congress, summit,
  symposium, seminar), corporate (company event, awards, gala), consumer_show (public fair,
  hobby show, market), sport, concert, theatre (musical, comedy, family show), other.
- If the page has no events at all, return an empty list."""


class ExtractionError(Exception):
    pass


def _client():
    return anthropic.Anthropic()


def extract_events(source, page_url, page_text, today):
    """Return a list of event dicts found in page_text."""
    user = (
        f"Today's date: {today}\n"
        f"Source: {source['name']} ({source['city']}), a {source['kind']}\n"
        f"Page: {page_url}\n\n"
        f"--- PAGE TEXT ---\n{page_text}\n--- END ---"
    )
    try:
        with _client().beta.messages.stream(
            model=MODEL,
            max_tokens=64000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={
                "effort": EFFORT,
                "format": {"type": "json_schema", "schema": EVENT_SCHEMA},
            },
            system=SYSTEM,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise ExtractionError("Anthropic API key missing or invalid (set ANTHROPIC_API_KEY)") from e
    except anthropic.RateLimitError as e:
        raise ExtractionError("Rate limited by the Anthropic API; try again later") from e
    except anthropic.APIStatusError as e:
        raise ExtractionError(f"Anthropic API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ExtractionError("Could not reach the Anthropic API") from e

    if msg.stop_reason == "refusal":
        raise ExtractionError("Model declined to process this page")
    if msg.stop_reason == "max_tokens":
        raise ExtractionError("Output hit max_tokens; page has too many events for one call")

    text = next((b.text for b in msg.content if b.type == "text"), None)
    if not text:
        raise ExtractionError("Empty response from model")
    try:
        events = json.loads(text)["events"]
    except (json.JSONDecodeError, KeyError) as e:
        raise ExtractionError(f"Could not parse model output: {e}") from e

    usage = msg.usage
    return events, {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}
