# Ready-made goods procurement

The `procurement-1` workflow plans a **supplier order** for one target sales day. It is separate from bakery production planning. A receipt of purchased goods is recorded as `received`, never as `produced`.

## Import contract

POST `/api/datasets` with `schema_version: "procurement-1"`, `source`, `bakery_id` (dataset/location identity), `target: "sold_units"`, `unit: "physical_unit"`, `provenance`, `catalog`, and `csv_text`. The catalog has one entry for each CSV `product_id`: `product_id`, `name`, positive integer `pack_size`, and optional nonnegative `max_order_qty`. These supplier settings must be entered explicitly; a test pack of one unit is not evidence of a real supplier pack.

Required CSV columns: `date,product_id,opening,received,sold,waste,closing`. Optional columns: `is_open` (`true`, `false`, `1`, `0`, or empty) and `stockout_status` (`none`, `occurred`, `unknown`, or empty). Empty status values remain unknown. Counts are nonnegative whole physical units. Every row must satisfy `opening + received = sold + waste + closing`; date/product pairs must be unique and product IDs must exactly match the catalog. Adjacent-date stock continuity differences are returned as warnings, without altering the source values.

`sold` is observed sales, including days with unknown opening status or stockout status. It is not a measured unconstrained demand label. A zero closing balance does not prove a stockout. Batch age and expiry are not inferred from daily totals.

## Forecast and order

POST `/api/plans` with the imported dataset ID, `method: "B1"` or `"ML"`, target date, cutoff, replay/operational mode, `lead_time_days`, and optional `usable_stock_assumption` keyed by product ID. Missing stock assumptions stay unknown; they are not treated as zero. In operational mode the order date is the current business day; in replay it is the day after cutoff. Target must be at least `lead_time_days` after the order date.

The plan separates:

1. `forecast_mean`: forecast **sales units** for the target day.
2. `recommended_qty`: suggested **order units** = uncovered forecast rounded up to supplier packs, then capped by `max_order_qty` if supplied.
3. `confirmed_qty`: explicit operator decision, initially empty. Confirmation validates packs, maximum, and closed target days, then saves a revision.

No automatic order is offered if usable target-day stock is unknown or suitable weekday history is absent. The user may still confirm a valid manual order. Exports have distinct sales, assumed-stock, suggested-order, and confirmed-order columns.

## ML boundary

Procurement uses a separate model protocol trained on observed unit sales. It never mixes artifacts with bakery production. Each model is trained from a dated snapshot; forecasts use only observations available through their cutoff. The model is gated per product by a frozen internal chronological check on the same B1 cohort. If that check does not improve MAE over B1, forecasting falls back to B1 for that product. The internal gate does not guarantee future accuracy; review a separate rolling holdout before operational use.
