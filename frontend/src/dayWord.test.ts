import { describe, expect, it } from "vitest";
import { dayWord } from "./dayWord";

describe("Russian day count", () => {
  it.each([
    [1, "день"], [2, "дня"], [5, "дней"],
    [11, "дней"], [12, "дней"], [14, "дней"],
    [21, "день"], [22, "дня"], [25, "дней"],
  ])("formats %i as %s", (value, expected) => {
    expect(dayWord(value)).toBe(expected);
  });
});
