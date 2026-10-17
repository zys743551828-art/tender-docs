import { useCallback, useEffect, useRef, useState } from "react";
import type {
  DocumentView,
  DownloadGrant,
  DocumentEvidenceView,
} from "./contracts";
import { PdfPreview } from "./PdfPreview";
import { api, parseNames, roleNames } from "./api";
import { Badge, ErrorNotice, Icon, Loading } from "./components";

export function Evidence({
  document,
  onClose,
}: {
  document: DocumentView;
  onClose: () => void;
}) {
  const [evidence, setEvidence] = useState<DocumentEvidenceView | null>(null);
  const [grant, setGrant] = useState<DownloadGrant | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [page, setPage] = useState(1);
  const [pageCount, setPageCount] = useState(0);
  const [downloading, setDownloading] = useState(false);
  const [errorAction, setErrorAction] = useState<"reload" | "download">(
    "reload",
  );
  const dialog = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const load = useCallback(async () => {
    setError(null);
    setErrorAction("reload");
    const results = await Promise.allSettled([
      api<DocumentEvidenceView>(`/documents/${document.id}/evidence`),
      api<DownloadGrant>(`/artifacts/${document.id}/download`),
    ]);
    if (results[0].status === "fulfilled") setEvidence(results[0].value.data);
    else setError(results[0].reason);
    if (results[1].status === "fulfilled") setGrant(results[1].value.data);
    else setError(results[1].reason);
  }, [document.id]);
  async function downloadOriginal() {
    setDownloading(true);
    setError(null);
    setErrorAction("download");
    try {
      const { data } = await api<DownloadGrant>(
        `/artifacts/${document.id}/download`,
      );
      const response = await fetch(data.download_url, { credentials: "omit" });
      if (!response.ok)
        throw new Error(
          `原件取回失败（${response.status}）。已保存的原件仍保留，请重试取件。`,
        );
      const url = URL.createObjectURL(await response.blob());
      const link = window.document.createElement("a");
      link.href = url;
      link.download = data.filename;
      link.style.display = "none";
      window.document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (failure) {
      setError(failure);
    } finally {
      setDownloading(false);
    }
  }
  useEffect(() => {
    void load().catch(setError);
  }, [load]);
  useEffect(() => {
    if (evidence?.coverage !== "pending") return;
    let active = true;
    const timer = window.setInterval(() => {
      void api<DocumentEvidenceView>(`/documents/${document.id}/evidence`)
        .then(({ data }) => {
          if (active) setEvidence(data);
        })
        .catch((failure: unknown) => {
          if (active) setError(failure);
        });
    }, 2000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [document.id, evidence?.coverage]);
  useEffect(() => {
    const before = window.document.activeElement as HTMLElement | null;
    const previousOverflow = window.document.body.style.overflow;
    window.document.body.style.overflow = "hidden";
    closeButton.current?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
      if (event.key === "Tab") {
        const elements = dialog.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], input, select, textarea, [tabindex="0"]',
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
    }
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.document.body.style.overflow = previousOverflow;
      before?.focus();
    };
  }, [onClose]);
  const pages = pageCount
    ? Array.from({ length: pageCount }, (_, index) => index + 1)
    : Array.from(
        new Set(evidence?.items.map((item) => item.physical_page) ?? []),
      );
  const isPdf =
    document.media_type === "application/pdf" ||
    document.filename.toLowerCase().endsWith(".pdf");
  return (
    <div className="dialog-backdrop">
      <div
        className="evidence-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="evidence-title"
        ref={dialog}
      >
        <header className="evidence-heading">
          <div>
            <span className="eyebrow">原件与解析依据</span>
            <h2 id="evidence-title">{document.filename}</h2>
            <p>
              {roleNames[document.role]} · 版本 {document.file_revision} ·{" "}
              {document.id === document.file_head_version_id
                ? "当前版本"
                : "历史版本"}
            </p>
          </div>
          <button
            ref={closeButton}
            className="button secondary"
            onClick={onClose}
          >
            关闭原件
          </button>
        </header>
        <div className="evidence-toolbar">
          <Badge
            tone={evidence?.coverage === "complete" ? "success" : "warning"}
          >
            {parseNames[evidence?.coverage ?? document.parse_coverage]}
          </Badge>
          {grant && (
            <button
              className="text-button"
              disabled={downloading}
              onClick={() => void downloadOriginal()}
            >
              {downloading ? "正在取回原件…" : "下载资料原件"}
              <Icon name="arrow" />
            </button>
          )}
        </div>
        <div className="evidence-error">
          <ErrorNotice
            error={error}
            onRetry={() =>
              errorAction === "download"
                ? void downloadOriginal()
                : void load().catch(setError)
            }
          />
        </div>
        {evidence?.gaps.length ? (
          <div className="notice warning evidence-gaps">
            <div>
              <strong>以下范围尚未完整核对</strong>
              <ul>
                {evidence.gaps.map((gap, index) => (
                  <li key={index}>{gap}</li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}
        {!isPdf && (
          <div className="notice warning evidence-gaps">
            <div>
              <strong>Word原件已保存，本次未完成对应检查</strong>
              <p>
                当前切片尚未提供Word转换。可返回资料页，将此修改稿的PDF作为独立文件上传。所选PDF有独立版本，不自动取得Word对应检查资格。
              </p>
            </div>
          </div>
        )}
        <div className="evidence-body">
          <section className="original-view" aria-label="原文件预览">
            {isPdf && grant ? (
              <>
                <PdfPreview
                  url={grant.download_url}
                  filename={document.filename}
                  page={page}
                  onPageCount={setPageCount}
                  onRefresh={() => void load().catch(setError)}
                />
                <p className="preview-hint">
                  定位到物理页 {page}
                  。当前显示真实整页，不绘制未经验证的坐标高亮。扫描及空白页也可切换查看。
                </p>
              </>
            ) : (
              <div className="empty-state">
                <Icon name="file" />
                <p>
                  {grant
                    ? "此格式暂不支持页内预览，可下载原件。"
                    : "正在取得私有原件访问地址…"}
                </p>
              </div>
            )}
          </section>
          <section className="extracted-view" aria-label="解析文本依据">
            <div className="section-heading">
              <h3>解析文本</h3>
              {pages.length > 0 && (
                <label className="page-select">
                  物理页
                  <select
                    value={page}
                    onChange={(event) => setPage(Number(event.target.value))}
                  >
                    {pages.map((value) => (
                      <option value={value} key={value}>
                        {value}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>
            <p className="muted">
              逐页对照原件。文本解析完成不等于规则完整提取或评审通过。
            </p>
            {!evidence && !error && <Loading />}
            {evidence && evidence.items.length === 0 && (
              <p className="small-empty">
                当前没有可展示的文本依据。请查看解析范围与原件。
              </p>
            )}
            {evidence &&
              evidence.items.length > 0 &&
              !evidence.items.some((item) => item.physical_page === page) && (
                <p className="small-empty">
                  此物理页未提取到文本，请查看原页及解析缺口。
                </p>
              )}
            {evidence?.items
              .filter((item) => item.physical_page === page)
              .map((item) => (
                <article className="evidence-quote" key={item.id}>
                  <small>
                    物理页 {item.physical_page}
                    {item.printed_label
                      ? ` · 印刷页码 ${item.printed_label}`
                      : ""}
                    {item.source_status === "withdrawn" ? " · 来源已撤回" : ""}
                  </small>
                  <p>{item.quote}</p>
                </article>
              ))}
            <details className="identity-details">
              <summary>文件身份</summary>
              <dl>
                <dt>版本ID</dt>
                <dd>{document.id}</dd>
                <dt>SHA-256</dt>
                <dd>{document.sha256}</dd>
                <dt>原件取得时间</dt>
                <dd>
                  {new Date(document.obtained_at).toLocaleString("zh-CN")}
                </dd>
              </dl>
            </details>
          </section>
        </div>
      </div>
    </div>
  );
}
