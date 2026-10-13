import type { Plan } from "./types";

export type PlanningInputs = {
  target: string;
  cutoff: string;
  method: string;
  targetOpen: boolean;
};

// Legacy plans may not have the requested method or explicit calendar flag.
// Never infer the requested method from a fallback or manual item.
export function planInputsChanged(plan: Plan, current: PlanningInputs) {
  return (
    plan.target_date !== current.target ||
    plan.cutoff !== current.cutoff ||
    (plan.requested_method != null &&
      plan.requested_method !== current.method) ||
    plan.items.some(
      (item) =>
        item.target_is_open != null &&
        item.target_is_open !== current.targetOpen,
    )
  );
}

export function forecastInputsChanged(
  snapshot: { targetDate: string; cutoff: string; method: string },
  current: { target: string; cutoff: string; method: string },
) {
  return (
    snapshot.targetDate !== current.target ||
    snapshot.cutoff !== current.cutoff ||
    snapshot.method !== current.method
  );
}
