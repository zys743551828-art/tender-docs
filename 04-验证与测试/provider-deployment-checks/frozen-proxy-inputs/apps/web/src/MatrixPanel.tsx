import { useState } from "react";
import type { MatrixView } from "./contracts";
import { Badge } from "./components";
import { CoverageSummary } from "./CoverageSummary";
import { EvidenceLinks } from "./EvidenceLinks";
import { ResourceState } from "./ResourceState";
import { useResource } from "./useResource";
import {
  confirmationNames,
  ruleKindNames,
  verdictNames,
} from "./workspaceTypes";

export function MatrixPanel({
  reviewId,
  bidId,
}: {
  reviewId: string | null;
  bidId: string;
}) {
  const resource = useResource<MatrixView>(
    reviewId ? `/reviews/${reviewId}/matrix` : null,
    5000,
  );
  const [filter, setFilter] = useState("all");
  if (resource.data?.rows.some((row) => row.rule.scope.bid_id !== bidId))
    return (
      <div className="notice error" role="alert">
        矩阵行不属于当前项目，未展示或采用其判定。请核对所选轮次。
      </div>
    );
  const rows =
    resource.data?.rows.filter(
      (row) =>
        filter === "all" ||
        (filter === "gap"
          ? row.check_status !== "succeeded" ||
            (row.verdict !== "satisfied" && row.verdict !== "not_applicable")
          : row.rule.kind === filter),
    ) ?? [];
  return (
    <div>
      <section className="panel">
        <div className="section-heading">
          <div>
            <h2>响应矩阵</h2>
            <p className="muted">
              执行状态与业务判断分开。未找到响应要看检查范围，检查失败不能显示为满足。
            </p>
          </div>
          <label className="compact-filter">
            筛选
            <select
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            >
              <option value="all">全部规则</option>
              <option value="gap">缺口与未完成</option>
              {Object.entries(ruleKindNames).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        </div>
        {!reviewId && (
          <p className="small-empty">
            尚无对应评审轮次。请先用明确正文开始评审；资料解析完成不会自动产生满足结论。
          </p>
        )}
        <ResourceState
          {...resource}
          hasData={!!resource.data}
          onRetry={() => void resource.reload()}
        />
        {resource.data && (
          <p className="matrix-count">
            本轮已列 {resource.data.rows.length} 条 · 当前筛选显示 {rows.length}{" "}
            条。筛选不改变覆盖分母或提交条件。
          </p>
        )}
      </section>
      {resource.data && (
        <CoverageSummary coverage={resource.data.coverage_layers} />
      )}
      {rows.map((row) => (
        <article className="panel matrix-row" key={row.rule.id}>
          <div className="matrix-row-heading">
            <Badge
              tone={
                row.rule.kind === "disqualification" ||
                row.rule.kind === "mandatory"
                  ? "warning"
                  : ""
              }
            >
              {ruleKindNames[row.rule.kind]}
            </Badge>
            <Badge>{confirmationNames[row.rule.confirmation]}</Badge>
          </div>
          <h3>{row.rule.statement}</h3>
          <div className="matrix-states">
            <div>
              <span>执行状态</span>
              <strong>
                {
                  {
                    pending: "未开始",
                    running: "正在核对",
                    succeeded: "执行已完成",
                    failed: "检查失败",
                    cancelled: "已取消",
                  }[row.check_status]
                }
              </strong>
            </div>
            <div>
              <span>业务判断</span>
              <strong>
                {row.check_status === "succeeded" && row.verdict
                  ? verdictNames[row.verdict]
                  : "尚未形成有效判断"}
              </strong>
            </div>
          </div>
          {row.error_code && (
            <div className="notice warning">
              本项检查失败（{row.error_code}），不能将该项计为满足。
            </div>
          )}
          <p className="small-note">适用范围：{row.rule.scope.description}</p>
          <EvidenceLinks ids={row.rule.evidence_ids} label="查看规则依据" />
          <EvidenceLinks ids={row.evidence_ids} label="查看响应与判定依据" />
          {row.finding_ids.length > 0 && (
            <p className="small-note">
              关联 {row.finding_ids.length} 项问题，风险清单保留各自处理状态。
            </p>
          )}
        </article>
      ))}
      {resource.data && rows.length === 0 && (
        <div className="panel small-empty">
          当前筛选没有行；请查看全部规则与本轮覆盖范围。
        </div>
      )}
    </div>
  );
}
