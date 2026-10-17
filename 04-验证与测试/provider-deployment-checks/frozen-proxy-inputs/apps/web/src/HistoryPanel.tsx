import { useCallback, useEffect, useRef, useState } from "react";
import type { DocumentView } from "./contracts";
import { api, parseNames, sizeLabel } from "./api";
import { Badge, ErrorNotice, Icon, Loading } from "./components";

export function HistoryPanel({
  head,
  onOpen,
  onClose,
}: {
  head: DocumentView;
  onOpen: (document: DocumentView) => void;
  onClose: () => void;
}) {
  const [items, setItems] = useState<DocumentView[]>([]);
  const [nextId, setNextId] = useState(head.previous_version_id);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const panel = useRef<HTMLElement>(null);
  const load = useCallback(
    async (id: string) => {
      setBusy(true);
      setError(null);
      try {
        const { data } = await api<DocumentView>(`/documents/${id}`);
        if (
          data.file_id !== head.file_id ||
          data.bid_id !== head.bid_id ||
          data.id !== id
        )
          throw new Error("历史文件身份不一致，请刷新资料后重试。");
        setItems((old) =>
          old.some((item) => item.id === data.id) ? old : [...old, data],
        );
        setNextId(data.previous_version_id);
      } catch (failure) {
        setError(failure);
      } finally {
        setBusy(false);
      }
    },
    [head.bid_id, head.file_id],
  );
  useEffect(() => {
    const previousFocus = window.document.activeElement as HTMLElement | null;
    panel.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    panel.current?.focus({ preventScroll: true });
    if (head.previous_version_id) void load(head.previous_version_id);
    return () => previousFocus?.focus();
  }, [head.previous_version_id, load]);
  return (
    <section
      className="panel history-panel"
      ref={panel}
      aria-label="文件历史版本"
      tabIndex={-1}
    >
      <div className="section-heading">
        <div>
          <h2>历史版本</h2>
          <p className="muted">
            当前文件：{head.filename} · 版本 {head.file_revision}
            。历史原件和解析依据按各自版本保留。
          </p>
        </div>
        <button className="text-button" onClick={onClose}>
          收起历史
        </button>
      </div>
      <ErrorNotice
        error={error}
        onRetry={nextId ? () => void load(nextId) : undefined}
      />
      {items.map((document) => (
        <article key={document.id} className="history-item">
          <div className="file-summary">
            <span className="document-icon">
              <Icon name="file" />
            </span>
            <div>
              <strong>{document.filename}</strong>
              <small>
                版本 {document.file_revision} · {sizeLabel(document.byte_size)}{" "}
                · {new Date(document.obtained_at).toLocaleString("zh-CN")}
              </small>
            </div>
          </div>
          <div className="history-state">
            <Badge>
              {document.id === document.file_head_version_id
                ? "当前版本"
                : "历史版本"}
            </Badge>
            <Badge
              tone={
                document.parse_coverage === "complete" ? "success" : "warning"
              }
            >
              {parseNames[document.parse_coverage]}
            </Badge>
          </div>
          <button className="button secondary" onClick={() => onOpen(document)}>
            查看此历史原件与依据
            <Icon name="arrow" />
          </button>
        </article>
      ))}
      {busy && <Loading text="正在读取历史版本…" />}
      {nextId && !busy && !error && (
        <div className="history-more">
          <button
            className="button secondary"
            onClick={() => void load(nextId)}
          >
            继续查看更早版本
          </button>
        </div>
      )}
      {!nextId && !busy && items.length > 0 && (
        <p className="history-end">
          已到此文件的最早版本。历史版本不继承当前稿的检查结论。
        </p>
      )}
    </section>
  );
}
