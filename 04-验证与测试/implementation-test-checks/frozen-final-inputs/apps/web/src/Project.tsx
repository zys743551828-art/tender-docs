import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import {
  api,
  ApiError,
  commandKey,
  fileSha256,
  parseNames,
  roleNames,
  sizeLabel,
} from "./api";
import type {
  Accepted,
  Bid,
  DocumentView,
  MaterialView,
  Operation,
  UploadBatchView,
  UploadBindingView,
  UploadGrant,
  UploadRequest,
  UploadTarget,
  UploadView,
} from "./contracts";
import { Badge, ErrorNotice, Icon, Loading } from "./components";
import { Evidence } from "./Evidence";
import { HistoryPanel } from "./HistoryPanel";

type DraftFile = {
  key: string;
  file: File;
  role: UploadRequest["role"];
  target: string;
  state: string;
  error?: unknown;
  grant?: UploadGrant;
  digest?: string;
  completeKey?: string;
  uploadKey: string;
  bytesSent?: boolean;
  refreshGrant?: boolean;
};
const uploadNames: Record<string, string> = {
  authorized: "等待上传",
  uploaded: "原件已收到",
  verifying: "正在核验原件",
  verified: "原件已核验，待入库",
  quarantined: "核验未通过",
  expired: "上传已过期",
  aborted: "上传已取消",
};
const operationNames: Record<string, string> = {
  queued: "等待处理",
  running: "正在处理",
  cancel_requested: "正在取消",
  succeeded: "处理已完成",
  failed: "处理失败",
  cancelled: "已取消",
};
function batchStorageKey(id: string) {
  return `tender:batch:${id}`;
}

export function Project({ id, onBack }: { id: string; onBack: () => void }) {
  const [bid, setBid] = useState<Bid | null>(null);
  const [etag, setEtag] = useState<string | null>(null);
  const [materials, setMaterials] = useState<MaterialView | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [batchId, setBatchId] = useState<string | null>(() =>
    localStorage.getItem(batchStorageKey(id)),
  );
  const [batch, setBatch] = useState<UploadBatchView | null>(null);
  const [files, setFiles] = useState<DraftFile[]>([]);
  const [busy, setBusy] = useState(false);
  const [partial, setPartial] = useState(false);
  const [operation, setOperation] = useState<Operation | null>(null);
  const [selected, setSelected] = useState<DocumentView | null>(null);
  const [historyHead, setHistoryHead] = useState<DocumentView | null>(null);
  const closeEvidence = useCallback(() => setSelected(null), []);
  const [conflict, setConflict] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const sectionRef = useRef<HTMLElement>(null);
  const pendingCommit = useRef<{
    signature: string;
    etag: string | null;
    key: string;
  } | null>(null);
  const load = useCallback(async () => {
    const [project, view] = await Promise.all([
      api<Bid>(`/bids/${id}`),
      api<MaterialView>(`/bids/${id}/materials`),
    ]);
    setBid(project.data);
    setEtag(project.etag);
    setMaterials(view.data);
  }, [id]);
  const refreshBatch = useCallback(async () => {
    if (!batchId) return;
    const { data } = await api<UploadBatchView>(
      `/bids/${id}/upload-batches/${batchId}`,
    );
    setBatch(data);
  }, [id, batchId]);
  useEffect(() => {
    void load().catch(setError);
  }, [load]);
  useEffect(() => {
    let active = true;
    const poll = async () => {
      if (!active) return;
      try {
        await refreshBatch();
        await load();
      } catch (e) {
        if (active) setError(e);
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 3000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [load, refreshBatch]);
  useEffect(() => {
    if (
      !operation ||
      !["queued", "running", "cancel_requested"].includes(operation.state)
    )
      return;
    const timer = window.setInterval(() => {
      void api<Operation>(`/operations/${operation.id}`)
        .then(({ data }) => setOperation(data))
        .catch(setError);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [operation]);
  function patchFile(key: string, patch: Partial<DraftFile>) {
    setFiles((old) =>
      old.map((file) => (file.key === key ? { ...file, ...patch } : file)),
    );
  }
  function chooseFiles(event: ChangeEvent<HTMLInputElement>) {
    const added = Array.from(event.target.files ?? []).map(
      (file): DraftFile => ({
        key: crypto.randomUUID(),
        uploadKey: crypto.randomUUID(),
        file,
        role: "tender",
        target: "",
        state: "待上传",
      }),
    );
    setFiles((old) => [...old, ...added]);
    event.target.value = "";
  }
  async function upload() {
    setBusy(true);
    setError(null);
    try {
      let currentBatch = batchId;
      if (!currentBatch || batch?.state === "committed") {
        const { data } = await api<UploadBatchView>(
          `/bids/${id}/upload-batches`,
          {
            method: "POST",
            body: {},
            key: commandKey(`batch:create:${id}`, { after: batchId }),
          },
        );
        currentBatch = data.id;
        setBatchId(data.id);
        setBatch(data);
        localStorage.setItem(batchStorageKey(id), data.id);
      }
      for (const item of files.filter((file) => file.state !== "已提交核验")) {
        try {
          if (!item.file.name.toLowerCase().endsWith(".pdf"))
            throw new Error("当前仅支持PDF，请从Word导出PDF后上传。");
          if (item.file.size === 0 || item.file.size > 100 * 1024 * 1024)
            throw new Error("单文件需大于0且不超过100 MiB，请拆分后上传。");
          patchFile(item.key, { state: "正在核对文件…", error: undefined });
          const digest = item.digest ?? (await fileSha256(item.file));
          patchFile(item.key, { digest });
          const targetDoc = materials?.documents.find(
            (document) => document.file_id === item.target,
          );
          const target: UploadTarget = targetDoc
            ? {
                kind: "new_version",
                file_id: targetDoc.file_id,
                expected_file_revision: targetDoc.file_head_revision,
              }
            : { kind: "new_file" };
          const request: UploadRequest = {
            batch_id: currentBatch,
            role: item.role,
            filename: item.file.name,
            byte_size: item.file.size,
            declared_sha256: digest,
            target,
          };
          let grant =
            item.grant ??
            (
              await api<UploadGrant>(`/bids/${id}/uploads`, {
                method: "POST",
                body: request,
                key: item.uploadKey,
              })
            ).data;
          if (
            !item.bytesSent &&
            (item.refreshGrant ||
              Date.parse(grant.expires_at) <= Date.now() + 5000)
          ) {
            const current = await api(`/uploads/${grant.upload_id}`);
            grant = (
              await api<UploadGrant>(
                `/uploads/${grant.upload_id}/authorizations`,
                {
                  method: "POST",
                  etag: current.etag,
                  body: { part_numbers: [1] },
                },
              )
            ).data;
          }
          const completeKey = item.completeKey ?? crypto.randomUUID();
          sessionStorage.setItem(
            `tender:upload:${grant.upload_id}`,
            JSON.stringify({
              digest,
              role: item.role,
              target: item.target,
              uploadKey: item.uploadKey,
              completeKey,
              filename: item.file.name,
              byteSize: item.file.size,
            }),
          );
          patchFile(item.key, { grant, completeKey, state: "正在上传原件…" });
          if (grant.mode !== "single" || !grant.upload_url)
            throw new Error(
              "本次文件需要分片上传，当前页面暂不支持。原有资料已保留，请使用较小文件。",
            );
          if (!item.bytesSent) {
            const response = await fetch(grant.upload_url, {
              method: "PUT",
              body: item.file,
              headers: {
                "Content-Type": item.file.type || "application/octet-stream",
              },
            });
            if (!response.ok) {
              if (response.status === 403)
                patchFile(item.key, { refreshGrant: true });
              throw new Error(
                `原件上传失败（${response.status}）。请重试此文件，其他已收文件会保留。`,
              );
            }
            patchFile(item.key, { bytesSent: true });
          }
          patchFile(item.key, { state: "正在提交核验…" });
          const { data } = await api<Accepted>(
            `/uploads/${grant.upload_id}/complete`,
            {
              method: "POST",
              body: { parts: [], declared_sha256: digest },
              key: completeKey,
            },
          );
          setOperation(data.operation);
          patchFile(item.key, { state: "已提交核验", error: undefined });
        } catch (e) {
          patchFile(item.key, { state: "此文件未完成", error: e });
        }
      }
      await refreshBatch();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function resumeUpload(upload: UploadView, file: File) {
    setBusy(true);
    setError(null);
    try {
      const record = sessionStorage.getItem(`tender:upload:${upload.id}`);
      if (!record)
        throw new Error(
          "此浏览器没有该文件的上传接续记录。已核验资料仍保留；可重新选择文件上传，并明确确认本批的有效集合。",
        );
      const saved = JSON.parse(record) as {
        digest: string;
        role: UploadRequest["role"];
        target: string;
        uploadKey: string;
        completeKey: string;
        filename: string;
        byteSize: number;
      };
      if (
        file.size !== saved.byteSize ||
        (await fileSha256(file)) !== saved.digest
      )
        throw new Error(
          "所选文件与本次上传的原件摘要不同。请选择原文件；修改稿请作为新版本上传。",
        );
      const current = await api<UploadView>(`/uploads/${upload.id}`);
      const { data: grant } = await api<UploadGrant>(
        `/uploads/${upload.id}/authorizations`,
        { method: "POST", etag: current.etag, body: { part_numbers: [1] } },
      );
      const restored: DraftFile = {
        key: crypto.randomUUID(),
        file,
        role: saved.role,
        target: saved.target,
        uploadKey: saved.uploadKey,
        completeKey: saved.completeKey,
        digest: saved.digest,
        grant,
        state: "待接续上传",
      };
      setFiles((old) => [
        ...old.filter((item) => item.grant?.upload_id !== upload.id),
        restored,
      ]);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function commit() {
    if (!batch) return;
    setBusy(true);
    setError(null);
    setConflict(false);
    const verified = batch.uploads.filter(
      (upload) => upload.state === "verified",
    );
    const body = {
      accepted_upload_ids: verified.map((upload) => upload.id),
      accepted_binding_ids: verified.map((upload) => upload.binding_id),
      explicit_partial_acceptance: partial,
    };
    const signature = JSON.stringify(body);
    if (pendingCommit.current?.signature !== signature) {
      pendingCommit.current = {
        signature,
        etag,
        key: commandKey(`batch:commit:${batch.id}`, { body, etag }),
      };
    }
    try {
      const { data } = await api<Accepted>(
        `/bids/${id}/upload-batches/${batch.id}/commit`,
        {
          method: "POST",
          etag: pendingCommit.current.etag,
          body,
          key: pendingCommit.current.key,
        },
      );
      setOperation(data.operation);
      setFiles([]);
      await refreshBatch();
      await load();
      setPartial(false);
    } catch (e) {
      setError(e);
      if (e instanceof ApiError && e.status === 412) {
        pendingCommit.current = null;
        setConflict(true);
      }
    } finally {
      setBusy(false);
    }
  }
  const verified =
    batch?.uploads.filter((upload) => upload.state === "verified") ?? [];
  const incomplete = batch
    ? batch.uploads.length !== verified.length ||
      files.some((file) => file.state !== "已提交核验")
    : false;
  const documents = materials?.documents ?? [];
  const heads = documents.filter(
    (document) => document.id === document.file_head_version_id,
  );
  if (!bid && !error) return <Loading text="正在打开项目…" />;
  return (
    <div className="page-content project-page">
      <button className="back-link" onClick={onBack}>
        <Icon name="back" />
        返回项目列表
      </button>
      <div className="page-heading">
        <div>
          <span className="eyebrow">项目工作区</span>
          <h1>{bid?.title ?? "项目读取失败"}</h1>
          <div className="project-meta">
            <Badge>
              {bid
                ? {
                    preparation: "准备资料",
                    working: "工作中",
                    submitted: "已登记提交",
                    closed: "已结项",
                    terminated: "已终止",
                  }[bid.phase]
                : "正在读取"}
            </Badge>
            {bid && <span>资料包 {bid.material_revision}</span>}
            <span>原件、版本与解析范围分别记录</span>
          </div>
        </div>
        <button
          className="button primary"
          onClick={() => {
            sectionRef.current?.scrollIntoView({ behavior: "smooth" });
            inputRef.current?.click();
          }}
        >
          <Icon name="upload" />
          上传资料
        </button>
      </div>
      <ErrorNotice
        error={error}
        onRetry={() => {
          setError(null);
          void load().catch(setError);
          void refreshBatch().catch(setError);
        }}
      />
      <div className="project-tabs" aria-label="项目工作区">
        <span className="project-tab active">项目资料</span>
        <span className="tab-description">
          从PDF原件开始，完成资料入库与范围核对
        </span>
      </div>
      <section className="next-step">
        <div className="next-step-icon">
          <Icon name="arrow" />
        </div>
        <div>
          <span className="eyebrow">当前下一步</span>
          <h2>
            {batch && batch.state !== "committed"
              ? "完成本批资料核验并确认入库"
              : documents.length
                ? "核对各份文件的解析范围"
                : "上传招标资料和投标正文"}
          </h2>
          <p>
            {documents.length
              ? "文本解析与规则评审分别记录。当前尚未形成风险结论或可提交判断。"
              : "可直接上传已有PDF，不需要先设置目录、模板或企业知识。"}
          </p>
        </div>
      </section>
      <section className="panel upload-panel" ref={sectionRef}>
        <div className="section-heading">
          <div>
            <h2>添加项目资料</h2>
            <p className="muted">
              先选择文件，再核对用途。单文件不超过100 MiB，原件保存在私有存储。
            </p>
          </div>
          <Badge>PDF 快速路径</Badge>
        </div>
        <label className="file-drop" htmlFor="project-files">
          <span className="upload-icon">
            <Icon name="upload" />
          </span>
          <strong>选择需要上传的资料</strong>
          <span>当前仅支持PDF；请从Word导出PDF后上传</span>
          <input
            ref={inputRef}
            id="project-files"
            type="file"
            accept=".pdf,application/pdf"
            multiple
            onChange={chooseFiles}
            disabled={busy}
          />
        </label>
        {files.length > 0 && (
          <div className="draft-files">
            {files.map((item) => (
              <div className="draft-file" key={item.key}>
                <div className="file-summary">
                  <Icon name="file" />
                  <div>
                    <strong>{item.file.name}</strong>
                    <small>
                      {sizeLabel(item.file.size)} · {item.state}
                    </small>
                  </div>
                </div>
                <label>
                  资料用途
                  <select
                    aria-label={`${item.file.name}的资料用途`}
                    value={item.role}
                    disabled={busy || !!item.grant}
                    onChange={(event) =>
                      patchFile(item.key, {
                        role: event.target.value as UploadRequest["role"],
                      })
                    }
                  >
                    {Object.entries(roleNames)
                      .filter(([role]) => role !== "official_result")
                      .map(([role, name]) => (
                        <option key={role} value={role}>
                          {name}
                        </option>
                      ))}
                  </select>
                </label>
                <label>
                  文件身份
                  <select
                    aria-label={`${item.file.name}的文件身份`}
                    value={item.target}
                    disabled={busy || !!item.grant}
                    onChange={(event) =>
                      patchFile(item.key, { target: event.target.value })
                    }
                  >
                    <option value="">作为独立文件</option>
                    {heads
                      .filter((document) => document.role === item.role)
                      .map((document) => (
                        <option key={document.file_id} value={document.file_id}>
                          {document.filename} · 新版本
                        </option>
                      ))}
                  </select>
                </label>
                {!item.grant && (
                  <button
                    className="text-button"
                    disabled={busy}
                    aria-label={`移除${item.file.name}`}
                    onClick={() =>
                      setFiles((old) =>
                        old.filter((file) => file.key !== item.key),
                      )
                    }
                  >
                    移除
                  </button>
                )}
                <div className="draft-error">
                  <ErrorNotice error={item.error} />
                </div>
              </div>
            ))}
            <div className="upload-actions">
              <span className="muted">
                同名文件不自动覆盖。需要替换时请选择对应文件的新版本。
              </span>
              <button
                className="button primary"
                disabled={
                  busy || files.every((file) => file.state === "已提交核验")
                }
                onClick={() => void upload()}
              >
                {busy
                  ? "正在处理…"
                  : files.some((file) => file.error)
                    ? "重试未完成的文件"
                    : "上传并核验原件"}
                <Icon name="arrow" />
              </button>
            </div>
          </div>
        )}
      </section>
      {batch && (
        <section className="panel batch-panel">
          <div className="section-heading">
            <div>
              <h2>
                {batch.state === "committed"
                  ? "本批资料已入库"
                  : "本批资料核验"}
              </h2>
              <p className="muted">
                传输完成后由服务端核对原件。核验通过不表示已完成评审。
              </p>
            </div>
            <Badge tone={batch.state === "committed" ? "success" : "warning"}>
              {batch.state === "committed" ? "已入库" : "待确认"}
            </Badge>
          </div>
          {batch.uploads.map((upload) => (
            <div className="batch-file" key={upload.id}>
              <div className="file-summary">
                <Icon name="file" />
                <div>
                  <strong>{upload.filename}</strong>
                  <small>
                    {batch.state === "committed" && upload.state === "verified"
                      ? "已核验并入库"
                      : uploadNames[upload.state]}
                    {upload.error_code ? ` · ${upload.error_code}` : ""}
                  </small>
                </div>
              </div>
              {upload.state === "authorized" && batch.state !== "committed" && (
                <label className="resume-file">
                  接续此文件
                  <input
                    aria-label={`重新选择${upload.filename}的原文件以接续上传`}
                    type="file"
                    accept=".pdf,application/pdf"
                    disabled={busy}
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      if (file) void resumeUpload(upload, file);
                      event.target.value = "";
                    }}
                  />
                </label>
              )}
              {upload.state === "verified" && batch.state !== "committed" && (
                <BindingRecovery
                  uploadId={upload.id}
                  documents={heads}
                  onUpdated={() => void refreshBatch().catch(setError)}
                />
              )}
            </div>
          ))}
          {batch.uploads.length === 0 && (
            <p className="muted">尚未收到本批文件。请继续上传已选资料。</p>
          )}
          {batch.state !== "committed" && verified.length > 0 && (
            <div className="batch-confirm">
              {incomplete && (
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={partial}
                    onChange={(event) => setPartial(event.target.checked)}
                  />
                  我确认仅将已核验的 {verified.length}{" "}
                  份资料入库；其余文件尚未入库
                </label>
              )}
              {conflict && (
                <div className="notice warning">
                  <div>
                    <strong>资料版本已变化，请重新核对</strong>
                    <p>
                      原件已保留。先刷新资料包；若目标文件已更新，请展开该文件的“调整入库目标”，选择新版本目标并说明原因。
                    </p>
                    <button
                      className="button secondary"
                      onClick={() => {
                        void load()
                          .then(() => {
                            setConflict(false);
                            setError(null);
                          })
                          .catch(setError);
                      }}
                    >
                      刷新并查看当前资料
                    </button>
                  </div>
                </div>
              )}
              <button
                className="button primary"
                disabled={busy || conflict || (incomplete && !partial)}
                onClick={() => void commit()}
              >
                {busy ? "正在确认…" : `确认 ${verified.length} 份资料入库`}
              </button>
            </div>
          )}
        </section>
      )}
      {operation && (
        <div className="operation-status" role="status">
          <Icon name="clock" />
          <span>
            {operationNames[operation.state]} ·{" "}
            {operation.kind === "parse_document" ? "资料解析" : "资料处理"}
            {["queued", "running"].includes(operation.state)
              ? "，可离开页面，稍后继续查看。"
              : "，文件状态以各份资料记录为准。"}
          </span>
        </div>
      )}
      <section className="panel materials-panel">
        <div className="section-heading">
          <div>
            <h2>
              已入库资料 <span className="count">{documents.length}</span>
            </h2>
            <p className="muted">
              查看每一个版本的原件与解析依据，不以文件名判断同稿关系。
            </p>
          </div>
        </div>
        {materials?.conflicts.map((message, index) => (
          <div className="notice warning" key={index}>
            {message}
          </div>
        ))}
        {!materials && <Loading />}
        {materials && documents.length === 0 && (
          <div className="small-empty">
            已核验文件经确认入库后，会在这里显示。
          </div>
        )}
        {documents.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>文件／版本</th>
                  <th>用途</th>
                  <th>解析范围</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {documents.map((document) => (
                  <tr key={document.id}>
                    <td>
                      <div className="file-summary">
                        <span className="document-icon">
                          <Icon name="file" />
                        </span>
                        <div>
                          <strong>{document.filename}</strong>
                          <small>
                            版本 {document.file_revision} ·{" "}
                            {sizeLabel(document.byte_size)}
                            {document.id !== document.file_head_version_id
                              ? " · 历史版本"
                              : " · 当前版本"}
                          </small>
                        </div>
                      </div>
                    </td>
                    <td>{roleNames[document.role]}</td>
                    <td>
                      <Badge
                        tone={
                          document.parse_coverage === "complete"
                            ? "success"
                            : "warning"
                        }
                      >
                        {parseNames[document.parse_coverage]}
                      </Badge>
                      {document.media_type.includes("word") && (
                        <small className="word-warning">
                          Word原件已保存，本次未完成对应检查
                        </small>
                      )}
                    </td>
                    <td>
                      <div className="document-actions">
                        <button
                          className="text-button"
                          onClick={() => setSelected(document)}
                        >
                          查看原件与依据
                          <Icon name="arrow" />
                        </button>
                        {document.previous_version_id && (
                          <button
                            className="text-button"
                            onClick={() =>
                              setHistoryHead((current) =>
                                current?.id === document.id ? null : document,
                              )
                            }
                            aria-expanded={historyHead?.id === document.id}
                          >
                            查看历史版本
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {historyHead && (
        <HistoryPanel
          key={historyHead.id}
          head={historyHead}
          onOpen={setSelected}
          onClose={() => setHistoryHead(null)}
        />
      )}
      <div className="notice neutral">
        <div>
          <strong>评审与评分尚未就绪</strong>
          <p>
            当前提供资料入库和PDF文本解析。核心规则核对、响应矩阵、完整评审、正式技术评分和提交检查尚未开放；文本解析完成不代表没有否决风险。
          </p>
        </div>
      </div>
      {selected && <Evidence document={selected} onClose={closeEvidence} />}
    </div>
  );
}

function BindingRecovery({
  uploadId,
  documents,
  onUpdated,
}: {
  uploadId: string;
  documents: DocumentView[];
  onUpdated: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const pending = useRef<{
    signature: string;
    etag: string | null;
    key: string;
  } | null>(null);
  async function rebind() {
    setBusy(true);
    setError(null);
    try {
      const { etag } = await api(`/uploads/${uploadId}`);
      const document = documents.find((item) => item.file_id === target);
      const body = {
        target: document
          ? {
              kind: "new_version",
              file_id: document.file_id,
              expected_file_revision: document.file_head_revision,
            }
          : { kind: "new_file" },
        reason: reason.trim(),
      };
      const signature = JSON.stringify(body);
      if (pending.current?.signature !== signature)
        pending.current = {
          signature,
          etag,
          key: commandKey(`upload:binding:${uploadId}`, { body, etag }),
        };
      await api<UploadBindingView>(`/uploads/${uploadId}/bindings`, {
        method: "POST",
        etag: pending.current.etag,
        body,
        key: pending.current.key,
      });
      onUpdated();
      pending.current = null;
      setOpen(false);
      setReason("");
    } catch (e) {
      setError(e);
      if (e instanceof ApiError && e.status === 412) pending.current = null;
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="binding-recovery">
      <button
        className="text-button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        调整入库目标
      </button>
      {open && (
        <div className="binding-fields">
          <p className="muted">
            复用已核验原件，不需要重新上传。只在确认新目标后建立新的绑定记录。
          </p>
          <label>
            入库目标
            <select
              value={target}
              onChange={(event) => setTarget(event.target.value)}
            >
              <option value="">作为独立文件</option>
              {documents.map((document) => (
                <option key={document.file_id} value={document.file_id}>
                  {document.filename} · 当前版本 {document.file_head_revision}{" "}
                  后的新版本
                </option>
              ))}
            </select>
          </label>
          <label>
            调整原因
            <input
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              maxLength={1000}
              required
            />
          </label>
          <ErrorNotice error={error} />
          <button
            className="button secondary"
            disabled={busy || !reason.trim()}
            onClick={() => void rebind()}
          >
            {busy ? "正在保存…" : "确认新的入库目标"}
          </button>
        </div>
      )}
    </div>
  );
}
