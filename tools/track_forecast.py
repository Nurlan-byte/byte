"""Register a paired BakePlan evaluation in a local MLflow SQLite database."""
import argparse
from contextlib import closing
from datetime import date
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import sqlite3

METRICS = ("n", "actual_sum", "baseline_mae", "candidate_mae", "baseline_bias_pct", "candidate_bias_pct")


def original_week_metrics(week):
    """Track the published pooled comparison and its zero/positive demand breakdown."""
    metrics = {}
    for group in ("all", "zero", "positive"):
        values = week[group]
        n = values["n"]
        if type(n) is not int or n < 0 or (group == "all" and n == 0):
            raise ValueError(f"Invalid original_week.{group}.n")
        metrics[f"original_week.{group}.n"] = float(n)
        for model in ("baseline", "candidate", "legacy"):
            key = f"{model}_ae_sum"
            error = values[key]
            if type(error) not in (int, float) or not math.isfinite(error) or error < 0 or (n == 0 and error != 0):
                raise ValueError(f"Invalid original_week.{group}.{key}")
            metrics[f"original_week.{group}.{key}"] = float(error)

    totals = week["all"]
    if week["zero"]["n"] + week["positive"]["n"] != totals["n"]:
        raise ValueError("Original week group counts do not add up")
    for model in ("baseline", "candidate", "legacy"):
        key = f"{model}_ae_sum"
        if not math.isclose(week["zero"][key] + week["positive"][key], totals[key], rel_tol=1e-10, abs_tol=1e-10):
            raise ValueError(f"Original week group errors do not add up for {model}")
        metrics[f"original_week.all.{model}_mae"] = totals[key] / totals["n"]
        if totals["legacy_ae_sum"] > 0:
            metrics[f"original_week.all.{model}_mae_change_vs_legacy_pct"] = (
                100 * (totals[key] - totals["legacy_ae_sum"]) / totals["legacy_ae_sum"]
            )
    return metrics


def summary_fields(report):
    if report.get("schema_version") != 1 or type(report["gate"]["passed"]) is not bool:
        raise ValueError("Invalid quality report schema or gate decision")
    fields = {"baseline_protocol": report["baseline_protocol"], "candidate": report["candidate"]}
    for name in ("protocol", "baseline_commit", "target"):
        if name in report:
            fields[name] = report[name]
    for path, fingerprint in report.get("fingerprints", {}).items():
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ValueError(f"Invalid code fingerprint for {path}")
        fields[f"code/{path}"] = fingerprint
    gate = report["gate"]
    improvement = gate["mean_relative_mae_improvement_pct"]
    if type(improvement) not in (int, float) or not math.isfinite(improvement):
        raise ValueError("Invalid holdout MAE improvement")
    metrics = {"holdout.mean_relative_mae_improvement_pct": float(improvement)}
    fields["gate.reasons"] = json.dumps(gate["reasons"], separators=(",", ":"))
    for name, value in gate["thresholds"].items():
        if name != "description":
            fields[f"gate.threshold.{name}"] = str(value)
    partitions = report["partitions"]
    dev, holdout = (partitions[p]["sources"] for p in ("development", "holdout"))
    if not dev or not holdout:
        raise ValueError("Both development and holdout need scored sources")
    for split, sources in (("development", dev), ("holdout", holdout)):
        for source, record in sources.items():
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", source):
                raise ValueError("Invalid source label")
            prefix = f"{split}.{source}"
            start, end = (date.fromisoformat(record[k]) for k in ("start", "end"))
            if start > end or (split == "holdout" and source in dev
                               and start <= date.fromisoformat(dev[source]["end"])):
                raise ValueError(f"Overlapping or inverted dates for {source}")
            if not re.fullmatch(r"[0-9a-f]{64}", record["data_sha256"]):
                raise ValueError(f"Missing dataset digest for {source}")
            for name in ("start", "end", "data_sha256"):
                fields[f"{prefix}.{name}"] = record[name]
            for name in METRICS:
                value = record[name]
                if value is None and name.endswith("bias_pct") and record["actual_sum"] == 0:
                    continue
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError(f"Invalid metric {prefix}.{name}")
                if (name.endswith("_mae") or name == "actual_sum") and value < 0:
                    raise ValueError(f"Negative {prefix}.{name}")
                if name == "n" and (type(value) is not int or value < 1):
                    raise ValueError(f"Invalid number of observations in {source}")
                metrics[f"{prefix}.{name}"] = float(value)
            metrics[f"{prefix}.mae_delta"] = record["candidate_mae"] - record["baseline_mae"]
    if "original_week" in report:
        week = report["original_week"]
        if date.fromisoformat(week["start"]) > date.fromisoformat(week["end"]):
            raise ValueError("Inverted original week dates")
        for name in ("start", "end"):
            fields[f"original_week.{name}"] = week[name]
        metrics.update(original_week_metrics(week))
    return fields, metrics


def record_run(report_path, database):
    raw = report_path.read_bytes()
    report = json.loads(raw)
    params, metrics = summary_fields(report)
    database = database.resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    if database.exists():
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables and not {"experiments", "runs"} <= tables:
            raise ValueError("Use a separate MLflow database; the file already contains application data")
    # Evaluation results stay on this machine. No ambient tracking URI or model registry is used.
    os.environ["MLFLOW_DISABLE_TELEMETRY"] = "true"
    from mlflow import MlflowClient
    from mlflow.entities import Metric, Param
    from mlflow.exceptions import MlflowException
    from time import time_ns

    client = MlflowClient(tracking_uri="sqlite:///" + database.as_posix())
    experiment_name = ("BakePlan receipt counts" if report.get("target") == "item_line_count"
                       else "BakePlan purchase forecasts")
    experiment = client.get_experiment_by_name(experiment_name)
    if experiment:
        experiment_id = experiment.experiment_id
    else:
        try:
            experiment_id = client.create_experiment(experiment_name, artifact_location=(database.parent / "mlflow-artifacts").as_uri())
        except MlflowException as exc:
            if exc.error_code != "RESOURCE_ALREADY_EXISTS":
                raise
            experiment_id = client.get_experiment_by_name(experiment_name).experiment_id
    fingerprint = sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
    tags = {"report_sha256": fingerprint, "gate": "passed" if report["gate"]["passed"] else "rejected"}
    run = client.create_run(experiment_id, run_name=params["candidate"], tags=tags)
    ident = run.info.run_id
    try:
        timestamp = time_ns() // 1_000_000
        client.log_batch(ident, metrics=[Metric(k, v, timestamp, 0) for k, v in metrics.items()],
                         params=[Param(k, str(v)) for k, v in params.items()])
        stored = client.get_run(ident).data
        if stored.metrics != metrics or stored.params != params or any(stored.tags.get(k) != v for k, v in tags.items()):
            raise RuntimeError("MLflow readback differs from the paired report")
    except Exception:
        client.set_terminated(ident, status="FAILED")
        raise
    client.set_terminated(ident)
    return {"run_id": ident, "experiment_id": experiment_id, "report_sha256": fingerprint,
            "gate": tags["gate"], "metrics_verified": len(metrics)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--database", type=Path, default=Path("data/forecast-experiments.sqlite3"))
    args = parser.parse_args()
    print(json.dumps(record_run(args.report, args.database), indent=2))


if __name__ == "__main__":
    main()
