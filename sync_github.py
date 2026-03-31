#!/usr/bin/env python3
"""
ClassPass → iCloud Calendar sync
Runs on GitHub Actions on a schedule.
"""

import os
import re
import json
import uuid
import base64
import requests
from datetime import datetime, timezone, timedelta

CLASSPASS_EMAIL = os.environ["CLASSPASS_EMAIL"]
CLASSPASS_PASSWORD = os.environ["CLASSPASS_PASSWORD"]
APPLE_ID = os.environ["APPLE_ID"]
APPLE_APP_PASSWORD = os.environ["APPLE_APP_PASSWORD"]
CALENDAR_NAME = os.environ.get("CALENDAR_NAME", "pilates 🤸")

CLASSPASS_BASE = "https://classpass.com"
CALDAV_BASE = "https://caldav.icloud.com"


def classpass_login_and_fetch():
    """Log in and fetch reservations entirely within the browser to avoid Cloudflare."""
    from playwright.sync_api import sync_playwright

    token = None
    user_id = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-GB",
        )
        page = context.new_page()

        def handle_response(response):
            nonlocal token, user_id
            if "_api/bff/v1" in response.url and response.status == 200:
                try:
                    body = response.json()
                    if isinstance(body, dict):
                        t = body.get("auth_token") or body.get("authToken")
                        if t:
                            token = t
                        u = body.get("id") or body.get("user_id") or body.get("userId")
                        if u:
                            user_id = u
                except Exception:
                    pass

        page.on("response", handle_response)

        print("→ Opening ClassPass login page...")
        page.goto(f"{CLASSPASS_BASE}/login", wait_until="networkidle")

        print("→ Dismissing consent banners...")
        page.wait_for_timeout(1000)
        page.evaluate(
            """
            ["trustarc-banner-overlay", "consent_blackbar"].forEach(function(id) {
                var el = document.getElementById(id);
                if (el) el.parentNode.removeChild(el);
            });
        """
        )

        print("→ Filling credentials...")
        page.fill('input[type="email"], input[name="email"]', CLASSPASS_EMAIL)
        page.fill('input[type="password"], input[name="password"]', CLASSPASS_PASSWORD)
        page.evaluate("document.querySelector(\"button[type='submit']\").click()")

        print("→ Waiting for login...")
        page.wait_for_function(
            "!window.location.href.includes('/login')", timeout=20000
        )
        print(f"→ Landed on: {page.url}")

        if not token:
            cp_sid = next(
                (c["value"] for c in context.cookies() if c["name"] == "CP.SID"), None
            )
            if cp_sid:
                try:
                    decoded = json.loads(base64.b64decode(cp_sid + "==").decode())
                    token = decoded.get("authToken")
                except Exception:
                    pass

        if not token:
            raise Exception("Could not extract auth token after login")

        print("→ Navigating to upcoming bookings...")
        page.goto(f"{CLASSPASS_BASE}/profile/upcoming", wait_until="networkidle")
        page.wait_for_timeout(2000)

        if not user_id:
            raise Exception("Could not determine user_id")

        print(f"→ Fetching reservations for user {user_id}...")
        raw = page.evaluate(
            f"""
            async () => {{
                const resp = await fetch('/_api/bff/v1/users/{user_id}/reservations', {{
                    headers: {{
                        'cp-authorization': 'Token {token}',
                        'content-type': 'application/json',
                        'platform': 'web',
                    }}
                }});
                return await resp.text();
            }}
        """
        )

        browser.close()

    try:
        data = json.loads(raw)
        reservations = (
            data
            if isinstance(data, list)
            else data.get("reservations", data.get("data", []))
        )
        print(f"✓ Found {len(reservations)} upcoming reservation(s)")
    except Exception as e:
        raise Exception(f"Could not parse reservations response: {e}\nRaw: {raw[:300]}")

    return reservations


def parse_reservation(r):
    def get(*keys):
        for k in keys:
            v = r.get(k)
            if v is not None:
                return v
        return None

    class_name = get("class_name", "className", "name", "title") or "ClassPass Class"
    studio = get("studio_name", "studioName", "venue_name", "venueName") or ""
    address = get("address", "studio_address", "location") or ""
    res_id = str(get("id", "reservation_id", "reservationId") or uuid.uuid4())
    start_str = get("starts_at", "start_time", "startTime", "start_datetime")
    end_str = get("ends_at", "end_time", "endTime", "end_datetime")

    if not start_str:
        return None

    try:
        start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
    except Exception:
        return None

    if end_str:
        try:
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        except Exception:
            end_dt = start_dt + timedelta(hours=1)
    else:
        duration = get("duration_minutes", "duration") or 60
        end_dt = start_dt + timedelta(minutes=int(duration))

    return {
        "id": res_id,
        "className": class_name,
        "studio": studio,
        "address": address,
        "startDate": start_dt.isoformat(),
        "endDate": end_dt.isoformat(),
    }


class CalDAVClient:
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.calendar_url = None

    def _request(self, method, url, body=None, headers=None):
        h = {"Content-Type": "application/xml; charset=utf-8"}
        if headers:
            h.update(headers)
        return self.session.request(method, url, data=body, headers=h)

    def discover(self):
        principal_url = None

        for url in [
            f"{CALDAV_BASE}/.well-known/caldav",
            f"{CALDAV_BASE}/",
        ]:
            try:
                resp = self._request(
                    "PROPFIND",
                    url,
                    """<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:">
  <d:prop><d:current-user-principal/></d:prop>
</d:propfind>""",
                    {"Depth": "0"},
                )
                print(f"DEBUG CalDAV {url}: {resp.status_code} {resp.text[:500]}")
                match = re.search(r"<[^>]*href[^>]*>(.*?)</[^>]*href>", resp.text)
                if match:
                    principal_url = match.group(1)
                    if not principal_url.startswith("http"):
                        principal_url = CALDAV_BASE + principal_url
                    break
            except Exception as e:
                print(f"DEBUG CalDAV {url} failed: {e}")

        if not principal_url:
            raise Exception("Could not find CalDAV principal")

        print(f"→ Principal URL: {principal_url}")

        body = """<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><c:calendar-home-set/></d:prop>
</d:propfind>"""
        resp = self._request("PROPFIND", principal_url, body, {"Depth": "0"})
        print(f"DEBUG calendar-home-set response: {resp.status_code} {resp.text[:500]}")

        home = re.search(
            r"calendar-home-set.*?<[^>]*href[^>]*>(.*?)</[^>]*href>",
            resp.text,
            re.DOTALL,
        )
        if not home:
            raise Exception("Could not find calendar home")
        home_url = home.group(1)
        if not home_url.startswith("http"):
            home_url = CALDAV_BASE + home_url

        print(f"→ Calendar home: {home_url}")

        body = """<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:displayname/>
    <c:supported-calendar-component-set/>
  </d:prop>
</d:propfind>"""
        resp = self._request("PROPFIND", home_url, body, {"Depth": "1"})
        print(f"DEBUG calendar list response: {resp.status_code} {resp.text[:500]}")

        calendars = []
        for response in re.finditer(
            r"<d:response>(.*?)</d:response>", resp.text, re.DOTALL
        ):
            block = response.group(1)
            if "VEVENT" not in block:
                continue
            href = re.search(r"<d:href>(.*?)</d:href>", block)
            dname = re.search(r"<d:displayname>(.*?)</d:displayname>", block)
            if href:
                url = (
                    href.group(1)
                    if href.group(1).startswith("http")
                    else CALDAV_BASE + href.group(1)
                )
                name = dname.group(1) if dname else ""
                calendars.append((name, url))

        print(f"✓ Available calendars: {[c[0] for c in calendars]}")
        match = next((c for c in calendars if c[0] == CALENDAR_NAME), None)
        if not match:
            raise Exception(
                f'Calendar "{CALENDAR_NAME}" not found. Available: {[c[0] for c in calendars]}'
            )

        self.calendar_url = match[1]
        print(f"✓ Using calendar: {CALENDAR_NAME}")
        return self.calendar_url

    def get_classpass_events(self):
        if not self.calendar_url:
            self.discover()

        now = datetime.now(timezone.utc)
        future = now + timedelta(days=60)

        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:getetag/>
    <c:calendar-data/>
  </d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{fmt_ical(now)}" end="{fmt_ical(future)}"/>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""

        resp = self._request("REPORT", self.calendar_url, body, {"Depth": "1"})
        events = []
        for response in re.finditer(
            r"<d:response>(.*?)</d:response>", resp.text, re.DOTALL
        ):
            block = response.group(1)
            ical = re.search(
                r"<c:calendar-data[^>]*>(.*?)</c:calendar-data>", block, re.DOTALL
            )
            href = re.search(r"<d:href>(.*?)</d:href>", block)
            if not ical or not href:
                continue
            ical_text = ical.group(1)
            if "CLASSPASS-SYNC" not in ical_text:
                continue
            uid = (re.search(r"UID:(.*?)[\r\n]", ical_text) or [None, None])[1]
            summary = (re.search(r"SUMMARY:(.*?)[\r\n]", ical_text) or [None, None])[1]
            dtstart = (
                re.search(r"DTSTART[^:]*:(.*?)[\r\n]", ical_text) or [None, None]
            )[1]
            url = (
                href.group(1)
                if href.group(1).startswith("http")
                else CALDAV_BASE + href.group(1)
            )
            events.append(
                {"href": url, "uid": uid, "summary": summary, "dtstart": dtstart}
            )

        print(f"✓ Found {len(events)} existing ClassPass event(s) in calendar")
        return events

    def create_event(self, booking):
        uid = f'classpass-{booking["id"]}-sync'
        ical = build_ical(booking, uid)
        url = self.calendar_url.rstrip("/") + f"/{uid}.ics"
        resp = self._request(
            "PUT",
            url,
            ical,
            {"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"},
        )
        if resp.status_code >= 400:
            raise Exception(f"PUT failed: {resp.status_code} {resp.text[:200]}")
        return uid

    def delete_event(self, href):
        resp = self.session.delete(href)
        if resp.status_code >= 400 and resp.status_code != 404:
            raise Exception(f"DELETE failed: {resp.status_code}")


def fmt_ical(dt):
    return dt.strftime("%Y%m%dT%H%M%SZ")


def build_ical(booking, uid):
    start = datetime.fromisoformat(booking["startDate"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(booking["endDate"].replace("Z", "+00:00"))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ClassPass Calendar Sync//EN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{fmt_ical(datetime.now(timezone.utc))}",
        f"DTSTART:{fmt_ical(start)}",
        f"DTEND:{fmt_ical(end)}",
        f'SUMMARY:{booking["className"]} @ {booking["studio"]}',
        f'LOCATION:{booking["address"]}',
        f"DESCRIPTION:ClassPass booking synced automatically",
        "CATEGORIES:CLASSPASS-SYNC",
        "X-CLASSPASS-SYNC:true",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines)


def sync():
    print("=== ClassPass Calendar Sync ===")

    raw = classpass_login_and_fetch()

    # Debug: print first reservation's keys so we can see the field names
    if raw:
        print(
            f"DEBUG first reservation keys: {list(raw[0].keys()) if isinstance(raw[0], dict) else raw[0]}"
        )

    bookings = [parse_reservation(r) for r in raw]
    bookings = [b for b in bookings if b]
    print(f"✓ Parsed {len(bookings)} valid booking(s)")


if __name__ == "__main__":
    sync()
