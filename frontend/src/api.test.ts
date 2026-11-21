import { expect, expectTypeOf, test, vi, afterEach } from "vitest";
import { api, confirmPlan, createForecast, createPlan, errorMessage } from "./api";
afterEach(() => vi.unstubAllGlobals());

test("forecast and order actions use typed quantities and require a keyed confirmation", () => {
  type Forecast = Awaited<ReturnType<typeof createForecast>>;
  type Plan = Awaited<ReturnType<typeof createPlan>>;
  type Confirmation = Parameters<typeof confirmPlan>[1];
  expectTypeOf<Forecast["items"][number]["forecast_mean"]>().toEqualTypeOf<number | null>();
  expectTypeOf<Plan["items"][number]["recommended_qty"]>().toBeNumber();
  expectTypeOf<Awaited<ReturnType<typeof confirmPlan>>["revision"]>().toBeNumber();
  expectTypeOf<Confirmation["quantities"][string]>().toBeNumber();
  expectTypeOf<{ revision: number; quantities: Record<string, number> }>().not.toExtend<Confirmation>();
});
test("CSV row correction is visible before expandable technical details", () => {
  const message = errorMessage(422, {
    error: {
      message: "CSV отклонён",
      details: ["Строка 2: Нарушен баланс остатков"],
    },
  });
  expect(message.split("\n")[0]).toContain("Строка 2: Нарушен баланс остатков");
  expect(message).toContain('["Строка 2: Нарушен баланс остатков"]');
});
test("structured error preserves useful server message and diagnostics", () => {
  expect(
    errorMessage(413, {
      error: {
        code: "too_large",
        message: "CSV превышает лимит",
        details: { limit: 100 },
      },
    }),
  ).toContain("CSV превышает лимит");
  expect(
    errorMessage(422, {
      error: { message: "Ошибка строк", details: ["строка 2"] },
    }),
  ).toContain("строка 2");
});
test("non-JSON error has safe HTTP fallback", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response("<html>proxy failure</html>", { status: 502 }),
      ),
  );
  await expect(api("/datasets")).rejects.toThrow("HTTP 502");
});
test("non-JSON 413 remains meaningful", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response("too large", { status: 413 })),
  );
  await expect(api("/datasets")).rejects.toThrow("Файл слишком большой");
});

test("mutating API calls carry the demo gateway CSRF header; reads remain headerless", async () => {
  const fetch = vi
    .fn()
    .mockImplementation(async () => new Response("{}", { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  const inputs = { dataset_id: "test", target_date: "2026-10-12", cutoff: "2026-10-11" };
  await createForecast(inputs);
  expect(fetch).toHaveBeenLastCalledWith("/api/forecasts", expect.objectContaining({
    method: "POST", body: JSON.stringify(inputs),
  }));
  await createPlan(inputs);
  expect(fetch).toHaveBeenLastCalledWith(
    "/api/plans",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify(inputs),
      headers: {
        "Content-Type": "application/json",
        "X-BakePlan-Request": "1",
      },
    }),
  );
  const confirmation = { revision: 0, idempotency_key: "retry-1", quantities: { p1: 6 } };
  await confirmPlan("id/1", confirmation);
  expect(fetch).toHaveBeenLastCalledWith("/api/plans/id%2F1/confirm", expect.objectContaining({
    method: "POST", body: JSON.stringify(confirmation),
  }));
  await api("/plans");
  expect(fetch).toHaveBeenLastCalledWith(
    "/api/plans",
    expect.objectContaining({ method: "GET", headers: undefined }),
  );
});

test("network failure is actionable and does not claim an uncertain write was rejected", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
  );
  await expect(confirmPlan("test", { revision: 0, idempotency_key: "same", quantities: { p1: 12 } })).rejects.toThrow(
    "проверьте его в истории",
  );
});
