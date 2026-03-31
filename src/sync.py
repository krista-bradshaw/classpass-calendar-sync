import sys
from datetime import datetime, timezone
from .config import APPLE_ID, APPLE_APP_PASSWORD
from .classpass import classpass_login_and_fetch, parse_reservation
from .caldav import CalDAVClient


def sync():
    print("=== ClassPass Calendar Sync ===")

    try:
        raw = classpass_login_and_fetch()
        bookings = [parse_reservation(r) for r in raw]
        bookings = [b for b in bookings if b]
        print(f"✓ Parsed {len(bookings)} valid booking(s)")

        client = CalDAVClient(APPLE_ID, APPLE_APP_PASSWORD)
        client.discover()
        existing = client.get_classpass_events()

        existing_keys = set()
        for ev in existing:
            if ev["summary"] and ev["dtstart"]:
                existing_keys.add(ev["summary"] + "|" + ev["dtstart"][:8])

        booking_keys = set()
        to_add = []
        for b in bookings:
            start = datetime.fromisoformat(b["startDate"].replace("Z", "+00:00"))
            title = f'{b["className"]} @ {b["studio"]}'
            key = title + "|" + start.strftime("%Y%m%d")
            booking_keys.add(key)
            if key not in existing_keys:
                to_add.append(b)

        to_remove = []
        for ev in existing:
            if ev["summary"] and ev["dtstart"]:
                key = ev["summary"] + "|" + ev["dtstart"][:8]
                if key not in booking_keys:
                    to_remove.append(ev)

        print(f"\n→ To add:    {len(to_add)}")
        print(f"→ To remove: {len(to_remove)}")

        created = 0
        removed = 0
        errors = []

        for b in to_add:
            try:
                client.create_event(b)
                print(
                    f'  ✓ Added: {b["className"]} @ {b["studio"]} on {b["startDate"][:10]}'
                )
                created += 1
            except Exception as e:
                msg = f'Failed to add "{b["className"]}": {e}'
                print(f"  ✗ {msg}")
                errors.append(msg)

        for ev in to_remove:
            try:
                client.delete_event(ev["href"])
                print(f'  ✓ Removed: {ev["summary"]}')
                removed += 1
            except Exception as e:
                msg = f'Failed to remove "{ev["summary"]}": {e}'
                print(f"  ✗ {msg}")
                errors.append(msg)

        print(f"\n=== Done: {created} added, {removed} removed, {len(errors)} errors ===")
        if errors:
            for e in errors:
                print(f"  ✗ {e}")
            sys.exit(1)
            
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        sys.exit(1)
