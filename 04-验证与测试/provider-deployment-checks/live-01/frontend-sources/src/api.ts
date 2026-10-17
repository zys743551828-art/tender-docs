import type { AuthView } from "./contracts";
import { implementedOperations } from "./contracts";

export function supportsApi(method: string, path: string) {
  const segments = `/api/v1${path.split("?")[0]}`.split("/");
  return implementedOperations.some((entry) => {
    const [verb, route] = entry.split(" ");
    const expected = route.split("/");
    return (
      verb === method.toUpperCase() &&
      expected.length === segments.length &&
      expected.every(
        (part, index) => part.startsWith("{") || part === segments[index],
      )
    );
  });
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code: string,
    public requestId?: string,
    public retryable = false,
    public details?: unknown,
  ) {
    super(message);
  }
}

let csrfToken = "";
export function commandKey(scope: string, request: unknown): string {
  const storageKey = `tender:command:${scope}`;
  const signature = JSON.stringify(request);
  const existing = sessionStorage.getItem(storageKey);
  if (existing) {
    try {
      const saved = JSON.parse(existing) as { signature: string; key: string };
      if (saved.signature === signature) return saved.key;
    } catch {
      /* A malformed browser entry cannot authorize an action. */
    }
  }
  const key = crypto.randomUUID();
  sessionStorage.setItem(storageKey, JSON.stringify({ signature, key }));
  return key;
}
export function completeCommand(scope: string) {
  sessionStorage.removeItem(`tender:command:${scope}`);
}
export function setAuth(auth: AuthView) {
  csrfToken = auth.csrf_token;
}

/** Byte grants may target our authenticated proxy or an external signed object URL. */
export function objectFetch(url: string, options: RequestInit = {}) {
  const sameOrigin =
    new URL(url, window.location.href).origin === window.location.origin;
  const headers = new Headers(options.headers);
  headers.delete("X-CSRF-Token");
  const method = (options.method ?? "GET").toUpperCase();
  if (sameOrigin && method !== "GET" && method !== "HEAD")
    headers.set("X-CSRF-Token", csrfToken);
  return fetch(url, {
    ...options,
    headers,
    credentials: sameOrigin ? "same-origin" : "omit",
  });
}

export type ApiResult<T> = { data: T; etag: string | null };
export async function api<T>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    etag?: string | null;
    key?: string;
  } = {},
): Promise<ApiResult<T>> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["X-CSRF-Token"] = csrfToken;
    headers["Idempotency-Key"] = options.key ?? crypto.randomUUID();
  }
  if (options.etag) headers["If-Match"] = options.etag;
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      method,
      credentials: "same-origin",
      headers,
      ...(options.body === undefined
        ? {}
        : { body: JSON.stringify(options.body) }),
    });
  } catch {
    throw new ApiError(
      "暂时无法连接服务。已完成的资料仍保留，请稍后重试。",
      0,
      "NETWORK_ERROR",
      undefined,
      true,
    );
  }
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    if (
      response.status === 401 &&
      path !== "/auth/login" &&
      path !== "/auth/session"
    ) {
      window.dispatchEvent(new Event("session-expired"));
    }
    throw new ApiError(
      error.message || `请求未完成（${response.status}），请重试。`,
      response.status,
      error.code || "REQUEST_FAILED",
      error.request_id,
      error.retryable ?? false,
      error.details,
    );
  }
  const data = response.status === 204 ? undefined : await response.json();
  return { data: data as T, etag: response.headers.get("ETag") };
}

export async function fileSha256(file: File) {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    await file.arrayBuffer(),
  );
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

export const roleNames: Record<string, string> = {
  tender: "招标文件",
  supplement: "补遗／答疑",
  drawing: "图纸",
  boq: "工程量清单",
  body: "投标正文",
  official_result: "官方结果",
  other: "其他参考",
};
export const parseNames: Record<string, string> = {
  pending: "等待解析",
  complete: "文本解析完成",
  partial: "解析范围不完整",
  failed: "解析失败",
};
export function sizeLabel(bytes: number) {
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
