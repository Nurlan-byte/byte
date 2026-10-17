import cgzCsv from "../../fixtures/cloud_game_zone_synthetic.csv?raw";
import cgzCatalog from "../../fixtures/cloud_game_zone_synthetic_catalog.json";
import {
  demoCatalog,
  parseCatalog,
  syntheticProcurementCsv,
} from "./procurement";

export const procurementDemos = [
  {
    id: "cgz-synthetic-demo",
    label: "Демо Cloud Game Zone — 6 товаров",
    csv: cgzCsv,
    catalog: cgzCatalog,
    provenance:
      "Полностью синтетический сценарий Cloud Game Zone; предварительные допущения",
  },
  {
    id: "procurement-demo",
    label: "Простой пример — 2 товара",
    csv: syntheticProcurementCsv(),
    catalog: parseCatalog(demoCatalog),
    provenance: "Синтетический пример закупки чая и сока",
  },
];
