"""Render agenda pages in a headless browser and return their visible text.

Many venue sites build their calendar with JavaScript, so a plain HTTP request
often returns an empty page. We render with Chromium and keep link targets
inline ("Event name <https://...>") so the extractor can report event URLs.
"""

import os
import re

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Inline each link's target after its text, drop invisible clutter.
_PAGE_TO_TEXT_JS = """
() => {
  document.querySelectorAll('script, style, noscript, svg, iframe').forEach(e => e.remove());
  document.querySelectorAll('a[href]').forEach(a => {
    const href = a.href;
    if (href.startsWith('http') && a.innerText.trim()) {
      a.appendChild(document.createTextNode(' <' + href + '> '));
    }
  });
  return document.body ? document.body.innerText : '';
}
"""


# Buttons that reveal more agenda items ("Toon meer", "Load more", ...).
_MORE_BUTTON = re.compile(r"^\s*(toon|laad|bekijk|meer|load|show|view)\b.*\b(meer|more|events?|evenementen)\b|^\s*meer\s*$", re.I)


def _expand(page, rounds=8):
    """Scroll and press 'show more' buttons so lazily loaded events appear."""
    for _ in range(rounds):
        page.mouse.wheel(0, 20000)
        page.wait_for_timeout(1200)
        button = page.get_by_role("button", name=_MORE_BUTTON)
        try:
            if button.count() == 0 or not button.first.is_visible():
                break
            button.first.click(timeout=3000)
            page.wait_for_timeout(2000)
        except PlaywrightError:
            break


def _launch_options():
    opts = {}
    # Locally (sandboxed) we may need a preinstalled Chromium and an HTTPS proxy.
    if os.environ.get("CHROMIUM_PATH"):
        opts["executable_path"] = os.environ["CHROMIUM_PATH"]
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        opts["proxy"] = {"server": proxy}
    return opts


def render_pages(urls, timeout_ms=45000):
    """Yield (url, final_url, text, error) for each URL."""
    with sync_playwright() as p:
        browser = p.chromium.launch(**_launch_options())
        context = browser.new_context(user_agent=USER_AGENT, locale="nl-NL")
        for url in urls:
            page = context.new_page()
            try:
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                # Give late-loading calendar widgets a moment.
                page.wait_for_timeout(2500)
                _expand(page)
                text = page.evaluate(_PAGE_TO_TEXT_JS)
                yield url, page.url, _tidy(text), None
            except PlaywrightError as e:
                # networkidle can time out on chatty sites; take what rendered.
                try:
                    text = page.evaluate(_PAGE_TO_TEXT_JS)
                except PlaywrightError:
                    text = ""
                if len(text) > 500 and not page.url.startswith("chrome-error:"):
                    yield url, page.url, _tidy(text), None
                else:
                    yield url, page.url, "", str(e).splitlines()[0]
            finally:
                page.close()
        browser.close()


def _tidy(text):
    lines = [" ".join(line.split()) for line in text.splitlines()]
    out, blank = [], False
    for line in lines:
        if not line:
            if not blank:
                out.append("")
            blank = True
        else:
            out.append(line)
            blank = False
    return "\n".join(out).strip()
