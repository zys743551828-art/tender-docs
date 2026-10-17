import { useEffect, useState } from "react";
import type {
  Accepted,
  Bid,
  FindingPage,
  FindingView,
  MaterialView,
  Operation,
  ReviewRequest,
  ReviewView,
} from "./contracts";
import { api, supportsApi } from "./api";
import { Badge, ErrorNotice } from "./components";
import { CoverageSummary } from "./CoverageSummary";
import { DecisionForm } from "./DecisionForm";
import { EvidenceLinks } from "./EvidenceLinks";
import { OperationCenter } from "./OperationCenter";
import { rememberOperation } from "./operationStorage";
import { ResourceState } from "./ResourceState";
import { ReviewIdentity } from "./ReviewIdentity";
import { NextActionControl } from "./NextActionControl";
import { useCommand } from "./useCommand";
import { useResource } from "./useResource";
import type { WorkspacePage } from "./workspaceTypes";
import { verdictNames } from "./workspaceTypes";

export function ReviewPanel({
  bid,
  reviewId,
  onNavigate,
  onChanged,
}: {
  bid: Bid;
  reviewId: string | null;
  onNavigate: (page: WorkspacePage, reviewId?: string) => void;
  onChanged: () => void;
}) {
  const resource = useResource<ReviewView>(
    reviewId ? `/reviews/${reviewId}` : null,
    5000,
  );
  const review = resource.data;
  if (review && review.bid_id !== bid.id)
    return (
      <div className="notice error" role="alert">
        此轮评审不属于当前项目，未展示或套用其结论。请从对应项目打开该轮次。
      </div>
    );
  return (
    <div>
      {!reviewId && (
        <section className="panel">
          <div className="section-heading">
            <div>
              <h2>尚未形成评审结果</h2>
              <p className="muted">
                已上传或已解析不表示完成规则核对。使用当前稿明确开始评审，服务不可用时会保留资料并说明缺口。
              </p>
            </div>
          </div>
        </section>
      )}
      <ResourceState
        {...resource}
        hasData={!!review}
        onRetry={() => void resource.reload()}
      />
      {review && !review.current && (
        <div className="notice warning">
          <div>
            <strong>历史评审 · 输入已固定</strong>
            <p>
              此轮对应原正文与原生效资料，不以当前稿冒充已复检。来源或规则更新后的影响应单独核对。
            </p>
          </div>
          {bid.current_review_id && bid.current_review_id !== review.id && (
            <button
              className="button secondary"
              onClick={() => onNavigate("review")}
            >
              打开当前评审
            </button>
          )}
        </div>
      )}
      {review && (
        <>
          <section className="panel review-summary">
            <div className="section-heading">
              <div>
                <h2>
                  {review.risk_count === null
                    ? "关键风险尚未形成完整计数"
                    : review.risk_count > 0
                      ? `本轮记录 ${review.risk_count} 项关键风险`
                      : "本轮未记录关键风险"}
                </h2>
                <p className="muted">
                  {review.core_coverage === "complete"
                    ? "本轮已识别规则的核对范围已处理，检测质量仍需单独评测。"
                    : "核心核对存在待处理、范围缺口或执行失败，不能认定全稿通过。"}
                </p>
              </div>
              <Badge
                tone={review.core_coverage !== "complete" ? "warning" : ""}
              >
                {
                  {
                    pending: "待处理",
                    complete: "已识别规则核对已完成",
                    partial: "核心覆盖不完整",
                    failed: "核心核对失败",
                  }[review.core_coverage]
                }
              </Badge>
            </div>
            <div className="review-summary-body">
              <ReviewIdentity reviewId={review.id} />
              <p>主要下一步：{review.next_action.reason}</p>
              <NextActionControl
                bidId={bid.id}
                action={review.next_action}
                onNavigate={onNavigate}
              />
              <div className="review-shortcuts">
                <button
                  className="text-button"
                  onClick={() => onNavigate("matrix", review.id)}
                >
                  打开本轮响应矩阵
                </button>
                <button
                  className="text-button"
                  onClick={() => onNavigate("materials")}
                >
                  上传修改稿与查看原件
                </button>
              </div>
              <p className="small-note">
                {review.prediction.mode === "hidden"
                  ? "预测数值按当前显示设置隐藏，风险与覆盖仍保留。"
                  : review.prediction.mode === "unavailable"
                    ? "当前不能可靠估分。"
                    : "评分与核心风险分别判断，模型验收与当前适用性单独核对。"}
              </p>
              {review.prediction.reasons.map((reason, index) => (
                <p className="small-note" key={index}>
                  {reason}
                </p>
              ))}
            </div>
          </section>
          <FindingsPanel
            bidId={bid.id}
            review={review}
            onNavigate={onNavigate}
          />
          <details className="coverage-details">
            <summary>查看完整分层覆盖、缺口与人工确认范围</summary>
            <CoverageSummary coverage={review.coverage_layers} />
          </details>
        </>
      )}
      {review ? (
        <details className="coverage-details">
          <summary>使用当前稿开始评审或明确复跑</summary>
          <ReviewStart bid={bid} onAccepted={onChanged} />
        </details>
      ) : (
        <ReviewStart bid={bid} onAccepted={onChanged} />
      )}
      {review && (
        <OperationCenter
          bidId={bid.id}
          currentOperationId={review.operation_id}
        />
      )}
    </div>
  );
}

export function ReviewStart({
  bid,
  onAccepted,
}: {
  bid: Bid;
  onAccepted: () => void;
}) {
  const materials = useResource<MaterialView>(`/bids/${bid.id}/materials`);
  const project = useResource<Bid>(`/bids/${bid.id}`);
  const [bodyId, setBodyId] = useState("");
  const [rerun, setRerun] = useState<string | null>(null);
  const [accepted, setAccepted] = useState<Accepted | null>(null);
  const operation = useResource<Operation>(
    accepted ? `/operations/${accepted.operation.id}` : null,
    3000,
  );
  const command = useCommand(`review:start:${bid.id}`);
  const body = bodyId || bid.current_body_id;
  const selected = materials.data?.documents.find(
    (document) => document.id === body,
  );
  const stale =
    project.data && project.data.material_revision !== bid.material_revision;
  async function start() {
    if (!body) return;
    const request: ReviewRequest = {
      body_version_id: body,
      material_revision: bid.material_revision,
      trigger: "manual",
      rerun_nonce: rerun,
    };
    const result = await command.run<Accepted>(
      `/bids/${bid.id}/reviews`,
      request,
      project.etag,
    );
    if (result) {
      setAccepted(result.data);
      rememberOperation(bid.id, result.data.operation.id);
      onAccepted();
    }
  }
  const state = operation.data?.state ?? accepted?.operation.state;
  const active =
    accepted &&
    (["queued", "running", "cancel_requested"].includes(state ?? "") ||
      (accepted.resource_kind === "pending_intent" &&
        state === "succeeded" &&
        !bid.current_review_id));
  return (
    <section className="panel review-start">
      <div className="section-heading">
        <div>
          <h2>使用当前稿开始评审</h2>
          <p className="muted">
            固定实际正文、生效资料和方法身份。无须先设置目录、模板或企业知识。
          </p>
        </div>
      </div>
      <div className="review-start-content">
        <ResourceState
          {...materials}
          hasData={!!materials.data}
          onRetry={() => void materials.reload()}
        />
        <label>
          本次评审正文
          <select
            value={body ?? ""}
            disabled={command.busy || !!active}
            onChange={(event) => setBodyId(event.target.value)}
          >
            <option value="">请选择明确的投标正文</option>
            {materials.data?.documents
              .filter((document) => document.role === "body")
              .map((document) => (
                <option key={document.id} value={document.id}>
                  {document.filename} · 文件版本 {document.file_revision}
                </option>
              ))}
          </select>
        </label>
        {selected && (
          <p className="small-note">
            资料修订 {bid.material_revision} · 本次固定 {selected.filename}{" "}
            的版本 {selected.file_revision}。解析缺口不因受理评审而消除。
          </p>
        )}
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={!!rerun}
            disabled={!!active}
            onChange={(event) =>
              setRerun(event.target.checked ? crypto.randomUUID() : null)
            }
          />
          明确要求同一输入另建一次复跑（普通开始会接续等价在途或既有记录）
        </label>
        <ErrorNotice
          error={command.error ?? project.error ?? operation.error}
        />
        {(command.conflict || stale) && (
          <div className="notice warning">
            <p>当前资料或正文已变化，请刷新并核对本次实际评审对象。</p>
            <button
              className="button secondary"
              onClick={() => {
                void project.reload();
                void materials.reload();
                onAccepted();
              }}
            >
              刷新并核对当前稿
            </button>
          </div>
        )}
        {accepted && (
          <div className="notice neutral" role="status">
            <div>
              <strong>
                {state === "failed"
                  ? "本次处理未完成，已保存资料保留"
                  : state === "cancelled"
                    ? "本次处理已取消，业务记录保留"
                    : accepted.resource_kind === "pending_intent"
                      ? "正在准备本轮要求，完成后自动开始评审"
                      : accepted.disposition === "reused"
                        ? "已接续此输入的处理记录"
                        : accepted.disposition === "pending_latest"
                          ? "新版资料已进入待评审请求"
                          : "本次评审已受理"}
              </strong>
              <p>
                可离开页面，稍后从项目恢复真实进度。受理和准备完成均不表示核心核对已通过。
              </p>
            </div>
          </div>
        )}
        <button
          className="button primary"
          disabled={
            command.busy ||
            !!active ||
            !body ||
            !project.etag ||
            !!stale ||
            !supportsApi("POST", `/bids/${bid.id}/reviews`)
          }
          onClick={() => void start()}
        >
          {command.busy
            ? "正在受理…"
            : active
              ? "本次处理已受理，请查看真实进度"
              : state === "failed"
                ? "重试并继续评审"
                : "开始评审所选正文"}
        </button>
      </div>
    </section>
  );
}

function FindingsPanel({
  review,
  onNavigate,
}: {
  bidId: string;
  review: ReviewView;
  onNavigate: (page: WorkspacePage, reviewId?: string) => void;
}) {
  const resource = useResource<FindingPage>(`/reviews/${review.id}/findings`);
  const [more, setMore] = useState<FindingPage[]>([]);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [filter, setFilter] = useState("open");
  useEffect(() => {
    setMore([]);
  }, [review.id, resource.data]);
  const all = [
    ...new Map(
      [
        ...(resource.data?.items ?? []),
        ...more.flatMap((page) => page.items),
      ].map((finding) => [finding.id, finding]),
    ).values(),
  ];
  const critical = all.filter(
    (finding) =>
      finding.severity === "critical" &&
      ["open", "deferred"].includes(finding.lifecycle),
  );
  const others = all.filter(
    (finding) =>
      !critical.some((item) => item.id === finding.id) &&
      (filter === "all" || ["open", "deferred"].includes(finding.lifecycle)),
  );
  const last = more.at(-1) ?? resource.data;
  async function next() {
    if (!last?.next_cursor) return;
    setLoadingMore(true);
    setLoadError(null);
    try {
      const { data } = await api<FindingPage>(
        `/reviews/${review.id}/findings?cursor=${encodeURIComponent(last.next_cursor)}`,
      );
      setMore((old) => [...old, data]);
    } catch (failure) {
      setLoadError(failure);
    } finally {
      setLoadingMore(false);
    }
  }
  const refresh = () => {
    void resource.reload();
  };
  return (
    <section className="findings-section" id="review-findings">
      <div className="section-title">
        <div>
          <h2>风险与问题</h2>
          <p className="muted">
            未解决关键风险固定可见，暂缓、采纳建议或关闭检测均不会自动解决。
          </p>
        </div>
        <label className="compact-filter">
          其他问题
          <select
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            <option value="open">待处理</option>
            <option value="all">全部及历史决定</option>
          </select>
        </label>
      </div>
      <ResourceState
        {...resource}
        hasData={!!resource.data}
        onRetry={refresh}
      />
      {critical.length > 0 && (
        <div className="critical-findings">
          <h3>尚未解决的关键风险 · 当前已加载 {critical.length} 项</h3>
          {critical.map((finding) => (
            <FindingCard
              key={finding.id}
              finding={finding}
              onNavigate={onNavigate}
              onSaved={refresh}
            />
          ))}
        </div>
      )}
      {others.map((finding) => (
        <FindingCard
          key={finding.id}
          finding={finding}
          onNavigate={onNavigate}
          onSaved={refresh}
        />
      ))}
      {resource.data && all.length === 0 && (
        <div className="panel small-empty">
          本轮当前没有返回问题记录。请结合来源范围、核心覆盖与检测质量核对；不表示零风险。
        </div>
      )}
      <ErrorNotice error={loadError} onRetry={() => void next()} />
      {last?.has_more && (
        <div className="load-more">
          <p className="small-note">
            仅显示已加载的 {all.length} 项，仍有未加载记录。
          </p>
          <button
            className="button secondary"
            disabled={loadingMore}
            onClick={() => void next()}
          >
            {loadingMore ? "正在加载…" : "加载更多问题"}
          </button>
        </div>
      )}
    </section>
  );
}

function FindingCard({
  finding,
  onNavigate,
  onSaved,
}: {
  finding: FindingView;
  onNavigate: (page: WorkspacePage, reviewId?: string) => void;
  onSaved: () => void;
}) {
  return (
    <article className={`panel finding-card severity-${finding.severity}`}>
      <div className="finding-heading">
        <Badge
          tone={
            finding.severity === "critical"
              ? "danger"
              : finding.severity === "major"
                ? "warning"
                : ""
          }
        >
          {
            {
              critical: "关键风险",
              major: "重要问题",
              minor: "一般问题",
              info: "改进建议",
            }[finding.severity]
          }
        </Badge>
        <span>
          {
            {
              open: "待处理",
              deferred: "暂缓，仍待处理",
              resolved_by_recheck: "有效复检已解决",
              dismissed: "有理由驳回，历史保留",
            }[finding.lifecycle]
          }
        </span>
      </div>
      <h3>{finding.statement}</h3>
      <p className="finding-verdict">判定：{verdictNames[finding.verdict]}</p>
      {finding.retained_known_critical &&
        finding.detection_run_state === "not_run_this_round" && (
          <div className="notice warning">
            先前检测发现，本轮未运行该检测；该风险仍保留待处理。
          </div>
        )}
      {finding.searched_ranges.length > 0 && (
        <details>
          <summary>已检查范围</summary>
          <ul>
            {finding.searched_ranges.map((range, index) => (
              <li key={index}>{range}</li>
            ))}
          </ul>
        </details>
      )}
      <EvidenceLinks
        ids={finding.evidence_ids}
        label="查看条款与正文引用原页"
      />
      {finding.origin_review_id !== finding.review_id && (
        <button
          className="text-button"
          onClick={() => onNavigate("review", finding.origin_review_id)}
        >
          查看发现此问题的历史轮次
        </button>
      )}
      <DecisionForm
        kind="findings"
        id={finding.id}
        revision={finding.decision_revision}
        evidenceIds={finding.evidence_ids}
        onSaved={onSaved}
      />
      {finding.decision && (
        <p className="small-note">
          已有决定：{finding.decision.reason}。决定与复检解决分别记录。
        </p>
      )}
    </article>
  );
}
