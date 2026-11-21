export type PlanningKind = "production" | "procurement";
export type ExpectedDelivery = {
  product_id: string;
  arrival_date: string;
  quantity: number;
};
export type DeliverySource = "manual" | "registry";
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
export type CountedDelivery = ExpectedDelivery & { id: string; number: string };
export type CatalogItem = {
  product_id: string;
  name: string;
  pack_size: number;
  max_order_qty: number | null;
};
export type DatasetTarget = "sold_units" | "item_line_count";
export type MeasurementUnit = "physical_unit" | "item_line";
export type OrderConstraint = "supplier_limit" | "system_limit";
export const overrideReasonLabels = {
  forecast: "Оценка спроса",
  stock: "Остаток на полке",
  event: "Мероприятие",
  supply: "Условия поставщика",
  other: "Другая причина",
} as const;
export type OverrideReason = keyof typeof overrideReasonLabels;
export type EvaluationApplicability = {
  applicable: boolean;
  reason: string | null;
  evaluated_horizon_days: number | null;
  requested_horizon_days: number;
};
export type Item = {
  pack_size?: number;
  max_order_qty?: number | null;
  evaluation_applicability?: EvaluationApplicability;
  product_id: string;
  name: string;
  batch_size: number;
  max_daily_qty: number;
  forecast_mean: number | null;
  recommended_qty: number;
  recommended_packs?: number;
  shortage_units?: number | null;
  constraint_reason?: OrderConstraint | null;
  confirmed_qty: number | null;
  override_reason?: OverrideReason;
  confirmed_packs?: number | null;
  confirmed_shortage_units?: number | null;
  confirmed_constraint_reason?: OrderConstraint | null;
  method: string;
  diagnostic_method?: string;
  target_is_open?: boolean;
  active_from?: string | null;
  active_to?: string | null;
  automatic_eligible?: boolean;
  usable_stock_assumption?: number | null;
  incoming_units?: number;
  unknown_open_n?: number;
  unknown_stockout_n?: number;
  max_constraint_active?: boolean;
  model_id?: string | null;
  fallback_reason?: string | null;
  warnings: string[];
  used_dates?: string[];
  n?: number;
};
export type PlanSummary = {
  planning_kind?: PlanningKind;
  order_date?: string;
  lead_time_days?: number;
  usable_stock_assumption?: Record<string, number>;
  expected_deliveries?: ExpectedDelivery[];
  delivery_source?: DeliverySource;
  registry_deliveries?: CountedDelivery[];
  id: string;
  revision: number;
  status: string;
  dataset_id: string;
  target_date: string;
  source: string;
  created_at?: string;
};
export type Plan = PlanSummary & {
  dataset_schema_version?: string;
  provenance?: string;
  items: Item[];
  cutoff: string;
  requested_method?: string;
  dataset_version?: string;
  bakery_id?: string;
  mode?: string;
  model_id?: string | null;
  model_version?: string;
  protocol_version?: string;
  target?: DatasetTarget;
  unit?: MeasurementUnit;
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
export type Forecast = {
  evaluation_applicability?: EvaluationApplicability;
  dataset_id: string;
  bakery_id: string;
  target: DatasetTarget;
  unit: MeasurementUnit;
  model_id: string | null;
  items: {
    evaluation_applicability?: EvaluationApplicability;
    product_id: string;
    forecast_mean: number | null;
    b1_mean: number | null;
    demand_features?: Record<string, number>;
    selection?: { observed_days: number; zero_days: number };
    reason?: string | null;
    method: string;
    fallback_reason?: string | null;
    used_dates?: string[];
    n?: number;
  }[];
};
