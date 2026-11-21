import type { paths } from "./generated/order-api";

type ForecastPost = paths["/api/forecasts"]["post"];
type CreatePlanPost = paths["/api/plans"]["post"];
type ConfirmPlanPost = paths["/api/plans/{ident}/confirm"]["post"];

export type ForecastRequest = ForecastPost["requestBody"]["content"]["application/json"];
export type Forecast = ForecastPost["responses"][200]["content"]["application/json"];
export type PlanRequest = CreatePlanPost["requestBody"]["content"]["application/json"];
export type Plan = CreatePlanPost["responses"][201]["content"]["application/json"];
export type ConfirmRequest = ConfirmPlanPost["requestBody"]["content"]["application/json"];
export type ConfirmPlanId = ConfirmPlanPost["parameters"]["path"]["ident"];
export type ConfirmedPlan = ConfirmPlanPost["responses"][200]["content"]["application/json"];
export type PlanMethod = NonNullable<PlanRequest["method"]>;
export type ForecastMethod = NonNullable<ForecastRequest["method"]>;
export type Item = Plan["items"][number];

export type PlanningKind = "production" | "procurement";
export type ExpectedDelivery = NonNullable<PlanRequest["expected_deliveries"]>[number];
export type DeliverySource = NonNullable<PlanRequest["delivery_source"]>;
export type RegisteredDelivery = {
  id: string;
  bakery_id: string;
  number: string;
  product_id: string;
  arrival_date: string;
  ordered_qty: number;
  received_qty: number;
  remaining_qty: number;
  status: "ordered" | "partial" | "received" | "cancelled";
};
export type CatalogItem = {
  product_id: string;
  name: string;
  pack_size: number;
  max_order_qty: number | null;
};
export type DatasetTarget = "sold_units" | "item_line_count";
export type MeasurementUnit = "physical_unit" | "item_line";
export type OrderConstraint = NonNullable<Item["constraint_reason"]>;
export type OverrideReason = NonNullable<ConfirmRequest["override_reasons"]>[string];
export const overrideReasonLabels = {
  forecast: "Оценка спроса",
  stock: "Остаток на полке",
  event: "Мероприятие",
  supply: "Условия поставщика",
  other: "Другая причина",
} satisfies Record<OverrideReason, string>;
export type EvaluationApplicability = NonNullable<Forecast["evaluation_applicability"]>;

// GET /plans returns a paginated summary, not a full plan.
export type PlanSummary = {
  planning_kind?: PlanningKind;
  order_date?: string | null;
  lead_time_days?: number | null;
  id: string;
  revision: number;
  status: string;
  dataset_id: string;
  target_date: string;
  source: string;
  created_at?: string;
};
export type Dataset = {
  forecast_only?: boolean;
  pos_provider?: string;
  pos_location_name?: string;
  product_names?: Record<string, string>;
  demand_context?: {
    calendar: { date: string }[];
    weather: { date: string }[];
    city?: string;
    calendar_source?: string;
    weather_source?: string;
  };

  planning_kind?: PlanningKind;
  catalog?: CatalogItem[];
  id: string;
  source: string;
  row_count?: number;
  rows?: { date: string }[];
  created_at?: string;
  missing_calendar_days?: number | null;
  stockout_count?: number | null;
  unknown_stockout_count?: number | null;
  diagnostics?: string[];
  bakery_id: string;
  target: DatasetTarget;
  unit: MeasurementUnit;
  provenance: string;
  schema_version: string;
  dataset_version?: string;
  version?: string;
  demand_context_base_version?: string;
  metadata_declared?: boolean;
};
export type ModelSummary = {
  id: string;
  status: "ready" | "not_ready";
  reason?: string | null;
  training_cutoff: string;
  target: DatasetTarget;
  unit: MeasurementUnit;
  bakery_id: string;
  dataset_id: string;
  dataset_version: string;
  protocol_version: string;
  evaluation?: {
    evaluation_role?: string;
    selection_performed?: boolean;
    selection_rule?: string;
    protocol?: string;
    experimental?: boolean;
    horizon_days?: number;
    feature_cutoff_policy?: string;
    first_holdout_cutoff?: string | null;
    embargoed_dates?: string[];
    coverage_denominator?: string;
    coverage_denominator_n?: number;
    paired_dates?: string[];
    paired_date_count?: number;
    paired_start?: string | null;
    paired_end?: string | null;
    paired_n?: number;
    b1_mae?: number | null;
    ml_mae?: number | null;
    coverage?: number;
    fit_end?: string | null;
    holdout_start?: string | null;
    holdout_end?: string | null;
  };
  products: Record<string, { status: string; reason?: string | null }>;
};
