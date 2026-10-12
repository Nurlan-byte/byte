import { expect, test } from "vitest";
import { validQuantity } from "./quantities";
test("batch limits and exact integer pieces", () => {
  expect(validQuantity("96", 12, 100)).toBe(true);
  expect(validQuantity("100", 12, 100)).toBe(false);
  expect(validQuantity("12.5", 12, 100)).toBe(false);
  expect(validQuantity("-12", 12, 100)).toBe(false);
  expect(validQuantity("0", 120, 100)).toBe(true);
});
