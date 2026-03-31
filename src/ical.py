from datetime import datetime, timezone


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
        f'DESCRIPTION:{booking.get("description", "ClassPass booking synced automatically")}',
        "CATEGORIES:CLASSPASS-SYNC",
        "X-CLASSPASS-SYNC:true",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines)
