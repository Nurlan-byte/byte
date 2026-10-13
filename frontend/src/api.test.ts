import { expect, test, vi, afterEach } from "vitest";
import { api, errorMessage } from "./api";
afterEach(() => vi.unstubAllGlobals());
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
