import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, roleNames, supportsApi } from "./api";
import type {
  DocumentView,
  RetrievalHit,
  RetrievalRequest,
  RetrievalResult,
} from "./contracts";
import { Badge, ErrorNotice, Loading } from "./components";
import { Evidence } from "./Evidence";

type Search = {
  key: string;
  request: RetrievalRequest;
  result: RetrievalResult;
};
type Metadata = { document?: DocumentView; error?: unknown };
export function RetrievalDrawer({
  bidId,
  title,
  onClose,
}: {
  bidId: string;
  title: string;
  onClose: () => void;
}) {
  const [query, setQuery] = useState(
    () => sessionStorage.getItem(`tender:retrieval-draft:${bidId}`) ?? "",
  );
  const [search, setSearch] = useState<Search | null>(null);
  const [busy, setBusy] = useState(false);
  const [opening, setOpening] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [retry, setRetry] = useState<
    | { request: RetrievalRequest }
    | { hit: RetrievalHit; historical: boolean }
    | null
  >(null);
  const [sourceChanged, setSourceChanged] = useState(false);
  const [metadata, setMetadata] = useState<Record<string, Metadata>>({});
  const [selected, setSelected] = useState<{
    document: DocumentView;
    page: number;
    historical: boolean;
  } | null>(null);
  const pending = useRef<{ signature: string; key: string } | null>(null);
  const panel = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const closeEvidence = useCallback(() => setSelected(null), []);
  const available = supportsApi("POST", "/retrievals");
  useEffect(() => {
    sessionStorage.setItem(`tender:retrieval-draft:${bidId}`, query);
  }, [bidId, query]);
  useEffect(() => {
    const before = window.document.activeElement as HTMLElement | null;
    const previousOverflow = window.document.body.style.overflow;
    window.document.body.style.overflow = "hidden";
    closeButton.current?.focus();
    return () => {
      window.document.body.style.overflow = previousOverflow;
      before?.focus();
    };
  }, []);
  useEffect(() => {
    function keydown(event: KeyboardEvent) {
      if (selected) return;
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
      if (event.key !== "Tab") return;
      const elements = panel.current?.querySelectorAll<HTMLElement>(
        'button:not(:disabled),a[href],input,textarea,select,[tabindex="0"]',
      );
      if (!elements?.length) return;
      const first = elements[0];
      const last = elements[elements.length - 1];
      if (event.shiftKey && window.document.activeElement === first) {
        event.preventDefault();
        last.focus();
      }
      if (!event.shiftKey && window.document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [selected, onClose]);
  useEffect(() => {
    if (!search) return;
    let active = true;
    setMetadata({});
    const ids = [
      ...new Set(search.result.hits.map((hit) => hit.source_version_id)),
    ];
    void Promise.all(
      ids.map(async (id): Promise<[string, Metadata]> => {
        try {
          return [
            id,
            { document: (await api<DocumentView>(`/documents/${id}`)).data },
          ];
        } catch (failure) {
          return [id, { error: failure }];
        }
      }),
    ).then((items) => {
      if (active) setMetadata(Object.fromEntries(items));
    });
    return () => {
      active = false;
    };
  }, [search]);
  async function retrieve(previous?: RetrievalRequest) {
    if (busy || opening || (!previous && !query.trim()) || !available) return;
    setBusy(true);
    setError(null);
    setRetry(null);
    const request: RetrievalRequest = previous ?? {
      query: query.trim(),
      context: "assistant",
      bid_id: bidId,
      limit: 8,
    };
    const signature = JSON.stringify(request);
    if (pending.current?.signature !== signature)
      pending.current = { signature, key: crypto.randomUUID() };
    try {
      const key = pending.current.key;
      const { data } = await api<RetrievalResult>("/retrievals", {
        method: "POST",
        body: request,
        key,
      });
      setSearch({ key, request, result: data });
      setSourceChanged(false);
      pending.current = null;
    } catch (failure) {
      setError(failure);
      setRetry({ request });
      if (failure instanceof ApiError && failure.status === 409) {
        setSourceChanged(true);
        pending.current = null;
      }
    } finally {
      setBusy(false);
    }
  }
  async function openSource(hit: RetrievalHit, historical = false) {
    if (!search || opening) return;
    setOpening(hit.citation_id);
    setError(null);
    setRetry(null);
    try {
      if (!historical)
        await api<RetrievalResult>("/retrievals", {
          method: "POST",
          body: search.request,
          key: search.key,
        });
      const { data } = await api<DocumentView>(
        `/documents/${hit.source_version_id}`,
      );
      if (data.id !== hit.source_version_id || data.bid_id !== bidId)
        throw new Error("引用版本身份不一致，未展示其他原件，请重新查证。");
      setSelected({ document: data, page: hit.physical_page, historical });
    } catch (failure) {
      setError(failure);
      setRetry({ hit, historical });
      if (failure instanceof ApiError && failure.status === 409)
        setSourceChanged(true);
    } finally {
      setOpening(null);
    }
  }
  return (
    <div className="retrieval-backdrop">
      <div
        className="retrieval-drawer"
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby="retrieval-title"
      >
        <header className="retrieval-heading">
          <div>
            <span className="eyebrow">按需资料查证</span>
            <h2 id="retrieval-title">查找原页依据</h2>
            <p>{title}</p>
          </div>
          <button
            ref={closeButton}
            className="button secondary"
            onClick={onClose}
          >
            关闭查证
          </button>
        </header>
        <div className="retrieval-content">
          <div className="notice neutral">
            <p>
              范围为当前项目可用资料。下方是检索摘录，不能替代完整核心核对或正式评分的完整章节输入。
            </p>
          </div>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void retrieve();
            }}
          >
            <label>
              想查证什么？
              <textarea
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                rows={3}
                required
                maxLength={4000}
                placeholder="例如：工期要求有哪些条件和例外"
              />
            </label>
            <button
              className="button primary"
              disabled={busy || !!opening || !query.trim() || !available}
            >
              {busy ? "正在查找原页依据…" : "查证当前项目资料"}
            </button>
          </form>
          {!available && (
            <div className="module-unavailable">
              <strong>资料查证服务尚未开放</strong>
              <p>
                查询草稿已保留。索引和检索服务就绪后可查证，不以空结果冒充已完成检索。
              </p>
            </div>
          )}
          <ErrorNotice
            error={error}
            onRetry={
              available && retry && !busy && !opening
                ? () => {
                    if ("request" in retry) void retrieve(retry.request);
                    else void openSource(retry.hit, retry.historical);
                  }
                : undefined
            }
          />
          {busy && <Loading text="正在检索已可用的来源…" />}
          {sourceChanged && search && (
            <div className="notice warning">
              <div>
                <strong>本次检索依据已变化</strong>
                <p>
                  下面保留此前查证记录，不能继续视为当前依据。请重新查证；确需追溯时可打开明确标识的历史原页。
                </p>
              </div>
            </div>
          )}
          {search && (
            <section className="retrieval-results" aria-label="查证结果">
              <div className="retrieval-result-heading">
                <h3>本次查证：{search.request.query}</h3>
                <Badge
                  tone={search.result.coverage === "available" ? "" : "warning"}
                >
                  {search.result.coverage === "available"
                    ? "检索范围可用"
                    : search.result.coverage === "partial"
                      ? "检索范围不完整"
                      : "当前检索范围不可用"}
                </Badge>
              </div>
              {query.trim() !== search.request.query && (
                <p className="small-note">
                  输入已修改，下方仍是上次问题的记录；重新查证后才使用新问题。
                </p>
              )}
              {search.result.gaps.length > 0 && (
                <div className="notice warning">
                  <ul>
                    {search.result.gaps.map((gap, index) => (
                      <li key={index}>{gap}</li>
                    ))}
                  </ul>
                </div>
              )}
              {search.result.hits.length === 0 && (
                <div className="small-empty">
                  本次没有返回可用摘录。请结合索引与读取缺口处理；这不证明原文不存在相关要求。
                </div>
              )}
              {search.result.hits.map((hit) => {
                const source = metadata[hit.source_version_id];
                return (
                  <article
                    className={`retrieval-hit${sourceChanged ? " outdated" : ""}`}
                    key={hit.citation_id}
                  >
                    <div className="retrieval-hit-heading">
                      <span className="hit-rank">{hit.rank}</span>
                      <div>
                        <strong>
                          {source?.document?.filename ?? "正在读取来源身份…"}
                        </strong>
                        <small>
                          物理页 {hit.physical_page}
                          {source?.document
                            ? ` · 文件版本 ${source.document.file_revision} · ${roleNames[source.document.role]}`
                            : ""}
                        </small>
                      </div>
                    </div>
                    <p>{hit.excerpt}</p>
                    {source?.document &&
                      source.document.id !==
                        source.document.file_head_version_id && (
                        <p className="small-note">
                          此文件已有较新版本，请重新核对当前来源；本条原页仍绑定检索时的实际版本。
                        </p>
                      )}
                    {!!source?.error && <ErrorNotice error={source.error} />}
                    <button
                      className="text-button"
                      disabled={busy || !!opening}
                      onClick={() => void openSource(hit, sourceChanged)}
                    >
                      {opening === hit.citation_id
                        ? "正在核对来源与读取原页…"
                        : sourceChanged
                          ? "查看当时原页（历史）"
                          : "核对来源并打开引用原页"}
                    </button>
                  </article>
                );
              })}
              <details className="identity-details">
                <summary>本次查证记录</summary>
                <dl>
                  <dt>记录标识</dt>
                  <dd>{search.result.trace_id}</dd>
                  <dt>依据政策身份</dt>
                  <dd>{search.result.policy_digest}</dd>
                </dl>
              </details>
            </section>
          )}
        </div>
        {selected && (
          <Evidence
            document={selected.document}
            initialPage={selected.page}
            contextNotice={
              selected.historical
                ? "正在追溯当时的检索原页；本次查证依据已变化，不能视为当前有效依据。"
                : undefined
            }
            onClose={closeEvidence}
          />
        )}
      </div>
    </div>
  );
}
