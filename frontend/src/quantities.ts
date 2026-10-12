export function validQuantity(
  value: string,
  batch: number,
  max: number,
): boolean {
  return (
    /^\d+$/.test(value) &&
    Number.isSafeInteger(Number(value)) &&
    Number(value) <= max &&
    Number(value) % batch === 0
  );
}
