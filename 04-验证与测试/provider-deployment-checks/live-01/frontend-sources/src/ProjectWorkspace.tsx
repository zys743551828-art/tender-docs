import { useCallback, useEffect, useState } from "react";
import type { Bid, MaterialView } from "./contracts";
import { Badge, Icon, Loading } from "./components";
import { FactsPanel } from "./FactsPanel";
import { MatrixPanel } from "./MatrixPanel";
import { OperationCenter } from "./OperationCenter";
import { RetrievalDrawer } from "./RetrievalDrawer";
import { Project } from "./Project";
import { ResourceState } from "./ResourceState";
import { ReviewPanel, ReviewStart } from "./ReviewPanel";
import { RulesPanel } from "./RulesPanel";
import { SettingsPanel } from "./SettingsPanel";
import { useResource } from "./useResource";
import { actionTarget } from "./workspaceTypes";
import { NextActionControl } from "./NextActionControl";
import { WorkspaceNavigation } from "./WorkspaceNavigation";
import { parseWorkspacePage, workspacePages } from "./workspaceTypes";
import type { WorkspacePage } from "./workspaceTypes";

export function ProjectWorkspace({
  id,
  route,
  onBack,
  onNavigate,
}: {
  id: string;
  route: string;
  onBack: () => void;
  onNavigate: (page: WorkspacePage, reviewId?: string) => void;
}) {
  const [retrievalOpen, setRetrievalOpen] = useState(false);
  const closeRetrieval = useCallback(() => setRetrievalOpen(false), []);
  const resource = useResource<Bid>(`/bids/${id}`, 5000);
  const bid = resource.data;
  const page = parseWorkspacePage(route.split("?")[0]);
  const explicitReview = new URLSearchParams(route.split("?")[1] ?? "").get(
    "review",
  );
  useEffect(() => {
    if (page === "home" && bid) {
      let saved: WorkspacePage | null = null;
      let savedReview: string | undefined;
      try {
        const value = localStorage.getItem(`tender:position:${id}`);
        if (value) {
          const position = JSON.parse(value) as {
            page: string;
            reviewId?: string;
          };
          if (workspacePages.some(([key]) => key === position.page)) {
            saved = position.page as WorkspacePage;
            savedReview = position.reviewId;
          }
        }
      } catch {
        /* Browser preferences do not establish a business state. */
      }
      onNavigate(
        saved ?? (bid.current_review_id ? "review" : "overview"),
        savedReview,
      );
    } else if (page !== "home")
      localStorage.setItem(
        `tender:position:${id}`,
        JSON.stringify({ page, reviewId: explicitReview ?? undefined }),
      );
  }, [page, bid, id, onNavigate, explicitReview]);
  const nav =
    page === "home" ? null : (
      <div className="workspace-nav-tools">
        <WorkspaceNavigation bidId={id} page={page} onNavigate={onNavigate} />
        <button
          className="button secondary"
          onClick={() => setRetrievalOpen(true)}
        >
          查证资料
        </button>
      </div>
    );
  const retrieval = retrievalOpen && (
    <RetrievalDrawer
      bidId={id}
      title={bid?.title ?? "当前项目"}
      onClose={closeRetrieval}
    />
  );
  if (page === "materials")
    return (
      <>
        <Project
          id={id}
          onBack={onBack}
          navigation={nav}
          reviewNotice={
            <div className="notice neutral">
              <div>
                <strong>资料入库与评审分别记录</strong>
                <p>
                  解析完成不表示核心规则已满足。进入评审工作面查看真实执行范围、风险与下一步；已有历史原件及评审身份保留。
                </p>
                <button
                  className="text-button"
                  onClick={() => onNavigate("review")}
                >
                  查看评审与风险
                </button>
              </div>
            </div>
          }
        />
        {retrieval}
      </>
    );
  if (!bid && resource.loading) return <Loading text="正在恢复项目工作区…" />;
  const reviewId = explicitReview ?? bid?.current_review_id ?? null;
  return (
    <div className="page-content project-page">
      <button className="back-link" onClick={onBack}>
        <Icon name="back" />
        返回项目列表
      </button>
      <div className="page-heading">
        <div>
          <span className="eyebrow">项目工作区</span>
          <h1>{bid?.title ?? "项目暂时无法读取"}</h1>
          {bid && (
            <div className="project-meta">
              <Badge>
                {
                  {
                    preparation: "准备资料",
                    working: "工作中",
                    submitted: "已登记提交",
                    closed: "已结项",
                    terminated: "已终止",
                  }[bid.phase]
                }
              </Badge>
              <span>资料修订 {bid.material_revision}</span>
              {explicitReview && <span>正在查看指定历史轮次</span>}
            </div>
          )}
        </div>
        {bid && (
          <button
            className="button secondary"
            onClick={() => onNavigate("materials")}
          >
            <Icon name="upload" />
            上传新版资料
          </button>
        )}
      </div>
      <ResourceState
        {...resource}
        hasData={!!bid}
        onRetry={() => void resource.reload()}
      />
      {nav}
      {retrieval}
      {bid && (
        <>
          {page === "overview" && (
            <Overview
              bid={bid}
              onNavigate={onNavigate}
              onChanged={() => void resource.reload()}
            />
          )}
          {page === "review" && (
            <ReviewPanel
              key={reviewId ?? "none"}
              bid={bid}
              reviewId={reviewId}
              onNavigate={onNavigate}
              onChanged={() => void resource.reload()}
            />
          )}
          {page === "matrix" && (
            <MatrixPanel
              key={reviewId ?? "none"}
              reviewId={reviewId}
              bidId={bid.id}
            />
          )}
          {page === "rules" && (
            <RulesPanel bid={bid} onChanged={() => void resource.reload()} />
          )}
          {page === "facts" && (
            <FactsPanel bid={bid} onChanged={() => void resource.reload()} />
          )}
          {page === "settings" && (
            <SettingsPanel
              bidId={bid.id}
              onSaved={() => void resource.reload()}
            />
          )}
          {page === "operations" && <OperationCenter bidId={bid.id} />}
        </>
      )}
    </div>
  );
}

function Overview({
  bid,
  onNavigate,
  onChanged,
}: {
  bid: Bid;
  onNavigate: (page: WorkspacePage, reviewId?: string) => void;
  onChanged: () => void;
}) {
  const materials = useResource<MaterialView>(
    `/bids/${bid.id}/materials`,
    5000,
  );
  const counts = materials.data?.documents;
  const next = bid.next_action;
  const page = actionTarget(bid.id, next)?.page;
  return (
    <div>
      <section className="next-step overview-next-step">
        <div className="next-step-icon">
          <Icon name="arrow" />
        </div>
        <div>
          <span className="eyebrow">当前主要下一步</span>
          <h2>{next.reason}</h2>
          <p>
            来自当前业务记录；资料、覆盖、风险和提交资格分别判断，不以任务成功代替通过。
          </p>
          {page && page !== "review" && (
            <NextActionControl
              bidId={bid.id}
              action={next}
              onNavigate={onNavigate}
            />
          )}
        </div>
      </section>
      <section className="panel">
        <div className="section-heading">
          <div>
            <h2>当前资料</h2>
            <p className="muted">
              已有PDF可直接开始；核对补遗是否完整，不按文件名或上传顺序推断生效关系。
            </p>
          </div>
          <button
            className="text-button"
            onClick={() => onNavigate("materials")}
          >
            打开资料工作面
          </button>
        </div>
        <ResourceState
          {...materials}
          hasData={!!materials.data}
          onRetry={() => void materials.reload()}
        />
        {counts && (
          <div className="material-overview">
            <article>
              <span>已入库文件</span>
              <strong>{counts.length}</strong>
            </article>
            <article>
              <span>招标与补遗</span>
              <strong>
                {
                  counts.filter(
                    (document) =>
                      document.role === "tender" ||
                      document.role === "supplement",
                  ).length
                }
              </strong>
            </article>
            <article>
              <span>投标正文</span>
              <strong>
                {counts.filter((document) => document.role === "body").length}
              </strong>
            </article>
            <article>
              <span>解析缺口或待处理</span>
              <strong>
                {
                  counts.filter(
                    (document) => document.parse_coverage !== "complete",
                  ).length
                }
              </strong>
            </article>
          </div>
        )}
        {materials.data?.conflicts.map((conflict, index) => (
          <div className="notice warning" key={index}>
            {conflict}
          </div>
        ))}
      </section>
      {bid.current_review_id ? (
        <section className="panel">
          <div className="section-heading">
            <div>
              <h2>已有可读取的评审轮次</h2>
              <p className="muted">
                打开风险、分层覆盖及对应原页，确认本轮是否仍属于当前资料。
              </p>
            </div>
            <button
              className="button primary"
              onClick={() => onNavigate("review")}
            >
              打开当前评审
            </button>
          </div>
        </section>
      ) : page === "review" ? (
        <ReviewStart bid={bid} onAccepted={onChanged} />
      ) : null}
    </div>
  );
}
