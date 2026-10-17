import { expect, test } from "vitest";
import { procurementDemos } from "./procurementDemos";

test.each([
  ["cgz-synthetic-demo", 6, 546],
  ["procurement-demo", 2, 168],
] as const)(
  "%s contains a complete synthetic procurement example",
  (id, products, rows) => {
    const demo = procurementDemos.find((item) => item.id === id)!;
    expect(demo.provenance.toLowerCase()).toContain("синтетический");
    expect(demo.catalog).toHaveLength(products);
    const records = demo.csv.trim().split(/\r?\n/);
    expect(records.shift()?.split(",").slice(0, 7)).toEqual([
      "date",
      "product_id",
      "opening",
      "received",
      "sold",
      "waste",
      "closing",
    ]);
    expect(records).toHaveLength(rows);
    const ids = new Set<string>();
    for (const row of records) {
      const [, product, opening, received, sold, waste, closing] =
        row.split(",");
      ids.add(product);
      expect(Number(opening) + Number(received)).toBe(
        Number(sold) + Number(waste) + Number(closing),
      );
    }
    expect([...ids].sort()).toEqual(
      demo.catalog.map((item) => item.product_id).sort(),
    );
  },
);
