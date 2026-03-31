import json
import base64
import uuid
from datetime import datetime, timezone, timedelta
from .config import CLASSPASS_EMAIL, CLASSPASS_PASSWORD, CLASSPASS_BASE


def classpass_login_and_fetch():
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
        page.goto(f"{CLASSPASS_BASE}/login", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        print("→ Dismissing consent banners...")
        page.evaluate(
            """
            ["trustarc-banner-overlay", "consent_blackbar"].forEach(function(id) {
                var el = document.getElementById(id);
                if (el) el.parentNode.removeChild(el);
            });
        """
        )
        page.wait_for_timeout(1000)

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
        page.goto(f"{CLASSPASS_BASE}/profile/upcoming", wait_until="domcontentloaded")
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
    res_id = str(r.get("id") or uuid.uuid4())

    class_obj = r.get("class") or {}
    venue_obj = r.get("venue") or {}

    class_name = class_obj.get("name") or class_obj.get("title") or "ClassPass Class"
    studio = venue_obj.get("name") or ""
    address_raw = venue_obj.get("address") or {}
    if isinstance(address_raw, dict):
        parts = [
            address_raw.get("address_line1", ""),
            address_raw.get("city", ""),
            address_raw.get("zip_code", ""),
        ]
        address = ", ".join(p for p in parts if p)
    else:
        address = str(address_raw)

    start_raw = r.get("starttime") or r.get("start_instant") or r.get("start_date")
    end_raw = r.get("endtime") or r.get("end_instant")

    if not start_raw:
        return None

    def parse_dt(val):
        if isinstance(val, (int, float)):
            return datetime.fromtimestamp(val, tz=timezone.utc)
        try:
            return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        except Exception:
            return None

    start_dt = parse_dt(start_raw)
    if not start_dt:
        return None

    end_dt = parse_dt(end_raw) if end_raw else start_dt + timedelta(hours=1)
    if not end_dt:
        end_dt = start_dt + timedelta(hours=1)

    return {
        "id": res_id,
        "className": class_name,
        "studio": studio,
        "address": address,
        "startDate": start_dt.isoformat(),
        "endDate": end_dt.isoformat(),
    }
