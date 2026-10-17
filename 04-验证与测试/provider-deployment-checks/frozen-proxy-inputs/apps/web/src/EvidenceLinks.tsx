import { useCallback, useEffect, useState } from "react";
import { api, roleNames, supportsApi } from "./api";
import type { DocumentView, EvidenceView } from "./contracts";
import { Badge, ErrorNotice, Loading } from "./components";
import { Evidence } from "./Evidence";

type Source = {
  id: string;
  evidence?: EvidenceView;
  document?: DocumentView;
  error?: unknown;
};
export function EvidenceLinks({
  ids,
  label = "查看引用原页",
}: {
  ids: string[];
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const [sources, setSources] = useState<Source[] | null>(null);
  const [selected, setSelected] = useState<Source | null>(null);
  const [attempt, setAttempt] = useState(0);
  const closeEvidence = useCallback(() => setSelected(null), []);
  const available = ids.length > 0 && supportsApi("GET", `/evidence/${ids[0]}`);
  const key = ids.join(",");
  useEffect(() => {
    if (!open || !available) return;
    let active = true;
    setSources(null);
    void Promise.all(
      key
        .split(",")
        .filter(Boolean)
        .map(async (id): Promise<Source> => {
          try {
            const { data: evidence } = await api<EvidenceView>(
              `/evidence/${id}`,
            );
            const { data: document } = await api<DocumentView>(
              `/documents/${evidence.document_version_id}`,
            );
            return { id, evidence, document };
          } catch (error) {
            return { id, error };
          }
        }),
    ).then((items) => {
      if (active) setSources(items);
    });
    return () => {
      active = false;
    };
  }, [open, available, key, attempt]);
  return (
    <div className="evidence-links">
      <button
        className="text-button"
        disabled={!available}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {label}
        {ids.length ? `（${ids.length}）` : ""}
      </button>
      {!available && (
        <small>
          {ids.length ? "原页定位服务尚未提供" : "此条尚无已关联依据"}
        </small>
      )}
      {open && (
        <div className="source-options" aria-label="引用来源">
          {!sources && <Loading text="正在读取引用来源…" />}
          {sources?.map((source) => (
            <div className="source-option" key={source.id}>
              {source.error ? (
                <ErrorNotice
                  error={source.error}
                  onRetry={() => setAttempt((old) => old + 1)}
                />
              ) : (
                <>
                  <div className="source-caption">
                    <strong>{source.document?.filename}</strong>
                    <Badge>{roleNames[source.document?.role ?? "other"]}</Badge>
                    {source.evidence?.source_status === "withdrawn" && (
                      <Badge tone="warning">来源已撤回</Badge>
                    )}
                  </div>
                  <p>{source.evidence?.quote}</p>
                  <button
                    className="text-button"
                    onClick={() => setSelected(source)}
                  >
                    打开物理页 {source.evidence?.physical_page} · 文件版本{" "}
                    {source.document?.file_revision}
                  </button>
                </>
              )}
            </div>
          ))}
        </div>
      )}
      {selected?.document && (
        <Evidence
          document={selected.document}
          initialPage={selected.evidence?.physical_page}
          onClose={closeEvidence}
        />
      )}
    </div>
  );
}
