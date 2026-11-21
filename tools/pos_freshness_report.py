"""Count fresh POS forecasts by the operator's local deadline, including days the scheduler never ran."""
import argparse
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import json
from math import ceil
from pathlib import Path
import sqlite3

from backend.pos_connection_store import BUSINESS_TZ


def report(database, connection_id, start, end, deadline, weekdays, as_of, *, target_percent=None):
    if start > end or as_of.utcoffset() is None or not weekdays or not set(weekdays) <= set(range(1, 8)):
        raise ValueError("Use an ordered date range, ISO weekdays 1–7 and an aware as-of instant")
    if deadline.tzinfo is not None:
        raise ValueError("Deadline uses the branch's local time (UTC+5)")
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.execute("BEGIN")
        epoch = db.execute("SELECT started_at,ended_at FROM pos_monitoring_epochs WHERE connection_id=?",
                           (connection_id,)).fetchone()
        if epoch is None:
            raise ValueError("Unknown monitored connection")
        fresh = dict(db.execute("""
            SELECT business_day,fresh_at FROM pos_fresh_days
            WHERE connection_id=? AND business_day BETWEEN ? AND ?
        """, (connection_id, str(start), str(end))))
    enrolled = datetime.fromisoformat(epoch[0])
    revoked = datetime.fromisoformat(epoch[1]) if epoch[1] else None
    rows = []
    for index in range((end - start).days + 1):
        day = start + timedelta(days=index)
        due = datetime.combine(day, deadline, tzinfo=BUSINESS_TZ)
        if day.isoweekday() not in weekdays or due < enrolled or (revoked and due >= revoked) or due > as_of:
            continue
        published = datetime.fromisoformat(fresh[str(day)]) if str(day) in fresh else None
        if published and published <= as_of:
            status = "on_time" if published <= due else "late"
        else:
            status = "missing"
        rows.append({"day": str(day), "status": status})
    counts = {s: sum(r["status"] == s for r in rows) for s in ("on_time", "late", "missing")}
    result = {"connection_id": connection_id, "deadline": str(deadline), "weekdays": sorted(weekdays),
              "as_of": as_of.isoformat(), "expected_days": len(rows), **counts,
              "on_time_pct": round(100 * counts["on_time"] / len(rows), 2) if rows else None,
              "days": rows}
    if target_percent is not None:
        try:
            target = Decimal(str(target_percent))
        except InvalidOperation as exc:
            raise ValueError("Target percent must be a number greater than 0 and at most 100") from exc
        if not target.is_finite() or not 0 < target <= 100:
            raise ValueError("Target percent must be a number greater than 0 and at most 100")
        # Compare whole days to the exact target, before rounding the displayed percentage.
        required = ceil(Fraction(target) * len(rows) / 100)
        status = "no_due_days"
        if rows:
            status = "met_to_date" if counts["on_time"] >= required else "missed_to_date"
        result["objective"] = {"target_percent": str(target), "status": status,
                               "required_on_time_days": required, "allowed_misses": len(rows) - required}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--connection", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--deadline", type=time.fromisoformat, required=True)
    parser.add_argument("--weekdays", default="1,2,3,4,5,6,7")
    parser.add_argument("--as-of", type=datetime.fromisoformat)
    parser.add_argument("--target-percent", help="Optional on-time objective for deadlines already due, e.g. 95")
    args = parser.parse_args()
    try:
        result = report(args.db, args.connection, args.start, args.end, args.deadline,
                        {int(v) for v in args.weekdays.split(",")}, args.as_of or datetime.now(UTC),
                        target_percent=args.target_percent)
    except (ValueError, sqlite3.Error) as err:
        parser.error(str(err))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
