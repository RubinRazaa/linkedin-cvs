"""Shared pieces: the stop signal and the pause between requests."""
import random
import re
import time
from datetime import datetime, timedelta

BASE = "https://www.linkedin.com"


class Stopped(Exception):
    """The run cannot continue. The message says why; nothing is retried."""


QUICK = (4, 9)      # seconds between requests; do not lower
SLOW = (15, 40)     # --slow: for unattended runs where time does not matter
REST_EVERY = 20     # --slow: after this many CVs...
REST = (120, 300)   # ...stop for this many seconds
_pace = QUICK


def set_slow(on):
    global _pace
    _pace = SLOW if on else QUICK


def job_id(value):
    """The job ID from an ID or from any LinkedIn job link."""
    m = re.search(r"(?<!\d)\d{8,12}(?!\d)", str(value))
    if not m:
        raise Stopped(f"no job ID in {value!r}: give the number or the job link")
    return m.group(0)


def slug(text):
    """Text made safe for a file or folder name."""
    return "_".join(re.sub(r"[^\w\- ]", "", text).split())


def pause():
    time.sleep(random.uniform(*_pace))


def deadline(until, now=None):
    """The next time the clock shows 'HH:MM' (today, or tomorrow if already past). None if unset."""
    if not until:
        return None
    now = now or datetime.now()
    hh, mm = datetime.strptime(until, "%H:%M").timetuple()[3:5]
    end = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return end if end > now else end + timedelta(days=1)
