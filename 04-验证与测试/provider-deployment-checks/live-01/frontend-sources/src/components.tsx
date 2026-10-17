import type { ReactNode } from "react";
import { ApiError } from "./api";

export function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark" aria-hidden="true">
        投
      </span>
      <span>
        投标工作台<small>市政工程 · AI WORKSPACE</small>
      </span>
    </div>
  );
}
export function Icon({
  name,
}: {
  name: "grid" | "file" | "upload" | "arrow" | "check" | "clock" | "back";
}) {
  const paths = {
    grid: (
      <>
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" />
      </>
    ),
    file: (
      <>
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <path d="M14 2v6h6M8 13h8M8 17h5" />
      </>
    ),
    upload: (
      <>
        <path d="M12 16V3m-5 5 5-5 5 5M3 15v5a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-5" />
      </>
    ),
    arrow: <path d="M5 12h14m-6-6 6 6-6 6" />,
    check: <path d="m5 12 4 4L19 6" />,
    clock: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </>
    ),
    back: <path d="M19 12H5m6-6-6 6 6 6" />,
  };
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name]}
    </svg>
  );
}
export function ErrorNotice({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  if (!error) return null;
  return (
    <div className="notice error" role="alert">
      <div>
        <strong>本次操作未完成</strong>
        <p>{error instanceof Error ? error.message : String(error)}</p>
        {error instanceof ApiError && (
          <small>
            {error.code}
            {error.requestId ? ` · 排查编号 ${error.requestId}` : ""}
          </small>
        )}
        {error instanceof ApiError &&
          error.details !== null &&
          typeof error.details === "object" &&
          Object.keys(error.details).length > 0 && (
            <details className="error-details">
              <summary>查看具体字段和影响范围</summary>
              <pre>{JSON.stringify(error.details, null, 2)}</pre>
            </details>
          )}
      </div>
      {onRetry && (
        <button className="button secondary" onClick={onRetry}>
          重试
        </button>
      )}
    </div>
  );
}
export function Badge({
  children,
  tone = "",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
export function Loading({ text = "正在读取…" }: { text?: string }) {
  return (
    <div className="loading" role="status">
      <span className="spinner" />
      {text}
    </div>
  );
}
