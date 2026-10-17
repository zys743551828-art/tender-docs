import { useRef, useState } from "react";
import { api, ApiError, commandKey, completeCommand, supportsApi } from "./api";

export function useCommand(scope: string) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const pending = useRef<{
    signature: string;
    key: string;
    etag: string | null;
  } | null>(null);
  async function run<T>(
    path: string,
    body: unknown,
    etag: string | null,
    method = "POST",
  ) {
    if (busy) return null;
    if (!supportsApi(method, path)) {
      setError(
        new ApiError("此操作尚未开放，输入已保留。", 501, "NOT_AVAILABLE"),
      );
      return null;
    }
    const signature = JSON.stringify({ path, method, body });
    if (pending.current?.signature !== signature)
      pending.current = {
        signature,
        etag,
        key: commandKey(scope, { signature, etag }),
      };
    setBusy(true);
    setError(null);
    try {
      const result = await api<T>(path, {
        method,
        body,
        etag: pending.current.etag,
        key: pending.current.key,
      });
      pending.current = null;
      completeCommand(scope);
      return result;
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 412)
        pending.current = null;
      setError(failure);
      return null;
    } finally {
      setBusy(false);
    }
  }
  return {
    busy,
    error,
    run,
    clearError: () => setError(null),
    conflict: error instanceof ApiError && error.status === 412,
  };
}
