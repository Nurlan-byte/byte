"""Persist issued decisions and subsequent sales in the same transaction as their source."""

from backend.branch_access import require_branch


def record_issue(db, result, items, issued_on):
    day = issued_on.isoformat()
    if not result["cutoff"] < day < result["target_date"]:
        return
    require_branch(result["bakery_id"])
    stored = db.execute("SELECT branch_id,source FROM datasets WHERE id=?", (result["dataset_id"],)).fetchone()
    if stored is None:
        raise ValueError("An issued forecast requires a saved dataset")
    if stored[0] is None:
        return  # A pre-assignment legacy dataset has no authoritative owner.
    if stored != (result["bakery_id"], result["source"]):
        raise ValueError("Forecast and sales dataset must have the same owner and source")
    rows = []
    for item in items:
        selection = item.get("selection", {})
        observed = selection.get("observed_days", 0)
        frequency = "unknown"
        if observed:
            frequency = "sparse" if 2 * selection["zero_days"] > observed else "regular"
        rows.append((
            result["dataset_id"], result["bakery_id"], result["source"], result["unit"],
            result["protocol_version"], day, result["cutoff"], result["target_date"],
            item["product_id"], item["method"], item["forecast_mean"], item["b1_mean"], frequency,
        ))
    db.executemany("""
        INSERT INTO issued_forecasts(dataset_id,bakery_id,source,unit,protocol,issued_on,
            cutoff,target_date,product_id,method,forecast,b1,frequency) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(dataset_id,protocol,cutoff,target_date,product_id) DO NOTHING
    """, rows)


def capture_actuals(db, dataset, observed_on):
    """Index explicit rows for previously issued product-days. Derived snapshots do not call this."""
    unit = dataset.get("unit")
    rows = dataset["rows"]
    if not rows or unit not in {"physical_unit", "item_line"}:
        return
    scope = (dataset["bakery_id"], dataset["source"], unit)
    day = observed_on.isoformat()
    pending = set(db.execute("""
        SELECT DISTINCT target_date,product_id FROM issued_forecasts
        WHERE bakery_id=? AND source=? AND unit=? AND target_date BETWEEN ? AND ? AND target_date<?
    """, (*scope, min(r["date"] for r in rows), max(r["date"] for r in rows), day)))
    if not pending:
        return
    field = "sold" if unit == "physical_unit" else "item_line_count"
    facts = []
    for row in rows:
        key = row["date"], row["product_id"]
        if key in pending and type(row.get(field)) is int and row[field] >= 0:
            status = "closed" if row.get("is_open") is False else row.get("stockout_status", "unknown")
            facts.append((dataset["id"], *scope, *key, row[field], status, day))
    db.executemany("""
        INSERT INTO forecast_actuals(dataset_id,bakery_id,source,unit,target_date,product_id,
            quantity,stockout_status,observed_on) VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(dataset_id,target_date,product_id) DO NOTHING
    """, facts)
