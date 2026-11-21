export const MAX_QUANTITY = 1_000_000_000;

export function validQuantity(
  value: string,
  batch: number,
  max: number,
): boolean {
  return (
    /^\d+$/.test(value) &&
    Number.isSafeInteger(Number(value)) &&
    Number(value) <= Math.min(max, MAX_QUANTITY) &&
    Number(value) % batch === 0
  );
}
