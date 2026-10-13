export function errorMessage(status: number, data: unknown): string {
  if (data && typeof data === "object" && "error" in data) {
    const error = data.error;
    if (
      error &&
      typeof error === "object" &&
      "message" in error &&
      typeof error.message === "string"
    ) {
      const details =
        "details" in error && error.details != null
          ? `\n${JSON.stringify(error.details)}`
          : "";
      return error.message + details;
    }
  }
  if (status === 413)
    return "Файл слишком большой. Уменьшите размер CSV и повторите импорт.";
  if (status === 409)
    return "План изменён или ключ подтверждения уже использован. Загрузите актуальную версию.";
  if (data && typeof data === "object" && "detail" in data)
    return JSON.stringify(data.detail);
  return `Сервер вернул ошибку HTTP ${status}. Повторите действие позже.`;
}
export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch("/api" + path, {
    method: body === undefined ? "GET" : "POST",
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    if (!response.ok) throw new Error(errorMessage(response.status, null));
    throw new Error(
      "Сервер вернул некорректный ответ. Повторите действие позже.",
    );
  }
  if (!response.ok) throw new Error(errorMessage(response.status, data));
  return data as T;
}
