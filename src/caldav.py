import re
import requests
from datetime import datetime, timezone, timedelta
from .config import CALDAV_BASE, CALENDAR_NAME
from .ical import fmt_ical, build_ical


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
                match = re.search(r"<href[^>]*>(.*?)</href>", resp.text)
                if match:
                    principal_url = match.group(1)
                    if not principal_url.startswith("http"):
                        principal_url = CALDAV_BASE + principal_url
                    break
            except Exception as e:
                print(f"DEBUG CalDAV {url} failed: {e}")

        if not principal_url:
            raise Exception("Could not find CalDAV principal")

        body = """<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><c:calendar-home-set/></d:prop>
</d:propfind>"""
        resp = self._request("PROPFIND", principal_url, body, {"Depth": "0"})
        home = re.search(
            r"calendar-home-set.*?<href[^>]*>(.*?)</href>", resp.text, re.DOTALL
        )
        if not home:
            raise Exception("Could not find calendar home")
        home_url = home.group(1)
        if not home_url.startswith("http"):
            home_url = CALDAV_BASE + home_url

        body = """<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:displayname/>
    <c:supported-calendar-component-set/>
  </d:prop>
</d:propfind>"""
        resp = self._request("PROPFIND", home_url, body, {"Depth": "1"})
        calendars = []
        for response in re.finditer(
            r"<response[^>]*>(.*?)</response>", resp.text, re.DOTALL
        ):
            block = response.group(1)
            if "VEVENT" not in block:
                continue
            href = re.search(r"<href[^>]*>(.*?)</href>", block)
            dname = re.search(r"<displayname[^>]*>(.*?)</displayname>", block)
            if href and dname:
                url = (
                    href.group(1)
                    if href.group(1).startswith("http")
                    else CALDAV_BASE + href.group(1)
                )
                name = dname.group(1)
                calendars.append((name, url))

        match = next((c for c in calendars if c[0] == CALENDAR_NAME), None)
        if not match:
            raise Exception(
                f'Calendar "{CALENDAR_NAME}" not found. Available: {[c[0] for c in calendars]}'
            )

        self.calendar_url = match[1]
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
            r"<response[^>]*>(.*?)</response>", resp.text, re.DOTALL
        ):
            block = response.group(1)
            ical = re.search(
                r"<calendar-data[^>]*>(.*?)</calendar-data>", block, re.DOTALL
            )
            href = re.search(r"<href[^>]*>(.*?)</href>", block)
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
