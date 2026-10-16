import { expect, test, vi, afterEach } from "vitest";
import { api, errorMessage } from "./api";
afterEach(() => vi.unstubAllGlobals());
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
  await api("/plans", { dataset_id: "test" });
  expect(fetch).toHaveBeenLastCalledWith(
    "/api/plans",
    expect.objectContaining({
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-BakePlan-Request": "1",
      },
    }),
  );
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
  await expect(api("/plans/test/confirm", { revision: 0 })).rejects.toThrow(
    "проверьте его в истории",
  );
});
