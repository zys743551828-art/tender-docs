import { ErrorNotice, Loading } from "./components";

export function ResourceState({
  unavailable,
  loading,
  error,
  hasData,
  onRetry,
  emptyLabel,
}: {
  unavailable?: boolean;
  loading?: boolean;
  error?: unknown;
  hasData: boolean;
  onRetry: () => void;
  emptyLabel?: string;
}) {
  if (unavailable)
    return (
      <div className="module-unavailable">
        <strong>此工作面尚未开放</strong>
        <p>
          相关服务尚未提供数据。已保存的资料与处理结果仍保留；这里暂不形成通过或完整覆盖结论。
        </p>
      </div>
    );
  if (error)
    return (
      <>
        <ErrorNotice error={error} onRetry={onRetry} />
        {hasData && (
          <p className="stale-data-note" role="status">
            暂时无法更新，下面保留上次读取的记录；请刷新核对后再作决定。
          </p>
        )}
      </>
    );
  if (loading && !hasData) return <Loading />;
  if (!hasData && emptyLabel)
    return <div className="small-empty">{emptyLabel}</div>;
  return null;
}
