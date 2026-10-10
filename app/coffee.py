"""coffee.py - the "buy me a coffee" pop-up.

AccessSlice is free and made by one person. Rules, decided by Edis
2026-10-10 (they replace the weekly Home-page card of 2026-09-07):

- A real pop-up: a modal dialog on the Home page - the page the
  program opens on - that the screen reader announces and that takes
  the focus. Never on an editor page in the middle of work.
- The first time is the first start at least a week after
  AccessSlice was installed; after that, once a month.
- "Buy me a coffee" opens the page in the user's normal browser
  (outside the AccessSlice window) and rests the pop-up for a month;
  "Not now", and closing the dialog with Escape, rest it for a month
  too. There is no setting that turns it off - the rest is the whole
  mechanism, and the quiet link on the Home page stays for anyone who
  wants it sooner.
- Nothing is tracked: the only thing that ever leaves the machine is
  the one click that opens the page.

Two ISO timestamps live in the user's config file: when AccessSlice
first ran (an install from before this rule starts its week at the
first start of the new version) and when the pop-up is next due.
"""

import datetime
import webbrowser

URL = "https://buymeacoffee.com/accessible3d"
FIRST_ASK_DAYS = 7
REST_DAYS = 30
CONFIG_KEY = "coffee_next_due"
FIRST_RUN_KEY = "first_run"


def _now(now):
    return now or datetime.datetime.now()


def _stamp(when):
    return when.replace(microsecond=0).isoformat()


def _parse(stamp):
    try:
        return datetime.datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None


def note_first_run(config, now=None):
    """Record the first start when it is missing or damaged; True when
    the config changed (the caller saves it)."""
    if _parse(config.get(FIRST_RUN_KEY)):
        return False
    config[FIRST_RUN_KEY] = _stamp(_now(now))
    return True


def due(config, now=None):
    """True when the pop-up should show: a week has passed since the
    first start, and the last answer's rest (if any) is over. A config
    without a first-start stamp is a first start - not due."""
    first = _parse(config.get(FIRST_RUN_KEY))
    if first is None:
        return False
    now = _now(now)
    if now < first + datetime.timedelta(days=FIRST_ASK_DAYS):
        return False
    next_due = _parse(config.get(CONFIG_KEY))
    return next_due is None or now >= next_due


def rest(config, now=None):
    """Rest the pop-up for REST_DAYS from now; returns the config."""
    when = _now(now) + datetime.timedelta(days=REST_DAYS)
    config[CONFIG_KEY] = _stamp(when)
    return config


def open_page(opener=None):
    """Open the coffee page in the default browser (looked up at call
    time, so a test can stand in for it)."""
    (opener or webbrowser.open)(URL)
