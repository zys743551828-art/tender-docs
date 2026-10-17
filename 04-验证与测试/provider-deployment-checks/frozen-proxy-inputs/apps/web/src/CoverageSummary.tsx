import type { CoverageLayer, ReviewCoverage } from "./contracts";
import { Badge } from "./components";

const stateNames = {
  pending: "待处理",
  complete: "本层已完成",
  partial: "范围不完整",
  failed: "执行失败",
  not_run: "未运行",
};
export function CoverageSummary({ coverage }: { coverage: ReviewCoverage }) {
  const layers: [string, CoverageLayer][] = [
    ["来源读取范围", coverage.source_scope],
    ["核心条款提取", coverage.core_extraction],
    ["已识别规则核对", coverage.judgment],
    ["评分条款处理", coverage.scoring],
  ];
  return (
    <section className="panel coverage-panel" aria-label="分层覆盖">
      <div className="section-heading">
        <div>
          <h2>本轮处理范围</h2>
          <p className="muted">
            来源读取、条款提取、规则判断与人工确认分别核对。
          </p>
        </div>
      </div>
      <div className="coverage-layers">
        {layers.map(([label, layer]) => (
          <article key={label}>
            <div className="coverage-label">
              <h3>{label}</h3>
              <Badge
                tone={
                  layer.state === "failed" || layer.state === "partial"
                    ? "warning"
                    : ""
                }
              >
                {stateNames[layer.state]}
              </Badge>
            </div>
            <p className="coverage-count">
              {layer.completed} <span>/ {layer.planned}</span>
            </p>
            <p className="muted">{layer.denominator_basis}</p>
            {layer.gaps.length > 0 && (
              <ul>
                {layer.gaps.map((gap, index) => (
                  <li key={index}>{gap}</li>
                ))}
              </ul>
            )}
          </article>
        ))}
      </div>
      <div className="coverage-footer">
        <span>待人工确认：{coverage.human_confirmation_pending} 项</span>
        <strong>
          {coverage.extraction_quality === "measured"
            ? "检测质量已单独评测，请查看对应评测范围"
            : "检测质量待验证"}
        </strong>
        <p>
          已识别规则核对完成不代表原文零漏提。人工确认条款也不表示正文已经满足。
        </p>
      </div>
    </section>
  );
}
