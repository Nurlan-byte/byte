export type Item = {
  product_id: string;
  name: string;
  batch_size: number;
  max_daily_qty: number;
  forecast_mean: number | null;
  recommended_qty: number;
  confirmed_qty: number | null;
  method: string;
  warnings: string[];
  used_dates?: string[];
  n?: number;
};
export type PlanSummary = {
  id: string;
  revision: number;
  status: string;
  dataset_id: string;
  target_date: string;
  source: string;
  created_at?: string;
};
export type Plan = PlanSummary & {
  items: Item[];
  cutoff: string;
  mode?: string;
};
export type Dataset = {
  id: string;
  source: string;
  row_count?: number;
  diagnostics?: string[];
};
