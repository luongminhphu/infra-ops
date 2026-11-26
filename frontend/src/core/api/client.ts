/** Fetch wrapper: JSON, CSRF header, and a typed error carrying the backend envelope. */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: unknown,
  ) {
    super(message);
  }
}

let csrfToken = "";
export function setCsrfToken(token: string): void {
  csrfToken = token;
}

const NETWORK_MESSAGE = "Không kết nối được máy chủ infra-ops. Kiểm tra mạng rồi thử lại.";

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  const method = (rest.method ?? "GET").toUpperCase();
  const h = new Headers(headers);
  h.set("Accept", "application/json");
  if (json !== undefined) h.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken) h.set("X-CSRF-Token", csrfToken);

  let res: Response;
  try {
    res = await fetch(path, {
      ...rest,
      method,
      headers: h,
      credentials: "same-origin",
      body: json !== undefined ? JSON.stringify(json) : rest.body,
    });
  } catch {
    throw new ApiError(0, "network_error", NETWORK_MESSAGE);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = undefined;
  try {
    data = text ? JSON.parse(text) : undefined;
  } catch {
    // non-JSON (e.g. proxy error page)
  }
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string; details?: unknown } } | undefined)?.error;
    throw new ApiError(
      res.status,
      err?.code ?? "http_error",
      err?.message ?? `Máy chủ trả về lỗi ${res.status}. Thử lại sau ít phút.`,
      err?.details,
    );
  }
  return data as T;
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Đã xảy ra lỗi không xác định.";
}

/** errorMessage plus the API's validation details ("field.path: msg; ..."), when present. */
export function errorDetails(error: unknown): string {
  if (error instanceof ApiError && Array.isArray(error.details)) {
    return `${error.message} ${(error.details as { loc: string[]; msg: string }[]).map((d) => `${d.loc.join(".")}: ${d.msg}`).join("; ")}`;
  }
  return errorMessage(error);
}

/** POST JSON and save the file the server returns (Content-Disposition name). Returns the response headers. */
export async function downloadFile(path: string, json: unknown, fallbackName: string): Promise<Headers> {
  const h = new Headers({ "Content-Type": "application/json" });
  if (csrfToken) h.set("X-CSRF-Token", csrfToken);
  let res: Response;
  try {
    res = await fetch(path, { method: "POST", headers: h, credentials: "same-origin", body: JSON.stringify(json) });
  } catch {
    throw new ApiError(0, "network_error", NETWORK_MESSAGE);
  }
  if (!res.ok) {
    const err = ((await res.json().catch(() => undefined)) as { error?: { code?: string; message?: string } } | undefined)?.error;
    throw new ApiError(res.status, err?.code ?? "http_error", err?.message ?? `Máy chủ trả về lỗi ${res.status}. Thử lại sau ít phút.`);
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(await res.blob());
  a.download = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? fallbackName;
  a.click();
  URL.revokeObjectURL(a.href);
  return res.headers;
}
