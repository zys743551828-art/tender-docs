import { useState } from "react";
import type { BatchRuleConfirmationView, Bid, RuleSetView } from "./contracts";
import { supportsApi } from "./api";
import { Badge, ErrorNotice } from "./components";
import { DecisionForm } from "./DecisionForm";
import { EvidenceLinks } from "./EvidenceLinks";
import { ResourceState } from "./ResourceState";
import { ManualRuleForm } from "./ManualRuleForm";
import { useCommand } from "./useCommand";
import { useResource } from "./useResource";
import { confirmationNames, ruleKindNames } from "./workspaceTypes";

export function RulesPanel({
  bid,
  onChanged,
}: {
  bid: Bid;
  onChanged: () => void;
}) {
  const resource = useResource<RuleSetView>(`/bids/${bid.id}/rules`);
  const [selected, setSelected] = useState<string[]>([]);
  const [manual, setManual] = useState(false);
  const [batchResult, setBatchResult] =
    useState<BatchRuleConfirmationView | null>(null);
  const command = useCommand(`rules:confirm:${resource.data?.id ?? bid.id}`);
  const rules = resource.data?.rules ?? [];
  const current = resource.data?.material_revision === bid.material_revision;
  const pending = rules.filter((rule) => rule.confirmation === "pending");
  const selectedPending = pending.filter((rule) => selected.includes(rule.id));
  function refresh() {
    void resource.reload();
    onChanged();
  }
  return (
    <div>
      <section className="panel">
        <div className="section-heading">
          <div>
            <h2>规则核对</h2>
            <p className="muted">
              核对条款解释、条件和例外；普通浏览不要求先确认全部条款。
            </p>
          </div>
          {resource.data && <Badge>{pending.length} 项待核对</Badge>}
        </div>
        <ResourceState
          {...resource}
          hasData={!!resource.data}
          onRetry={() => void resource.reload()}
        />
        {resource.data && (
          <div className="rules-intro">
            <p className="muted">
              规则包对应资料修订 {resource.data.material_revision} · 包内共{" "}
              {rules.length} 项。已识别清单不代表原文零漏提。
            </p>
            {!current && (
              <div className="notice warning">
                当前资料已变化，此包属于先前资料范围。请核对来源与影响后重新提取或评审。
              </div>
            )}
            {resource.data.coverage !== "complete" && (
              <div className="notice warning">
                {resource.data.coverage === "conflict"
                  ? "条款或来源存在冲突，需先核对。"
                  : "核心条款提取范围不完整，不能据此认定全稿已核对。"}
              </div>
            )}
            <button
              className="button secondary"
              disabled={
                !current ||
                !supportsApi("POST", `/bids/${bid.id}/rule-set-corrections`)
              }
              aria-expanded={manual}
              onClick={() => setManual(!manual)}
            >
              补充并确认条款
            </button>
            <p className="small-note">
              人工补入的核心要求保留来源身份，不会仅因模型重新提取未列出而消失。
            </p>
          </div>
        )}
      </section>
      {manual && resource.data && (
        <ManualRuleForm
          bid={bid}
          ruleSet={resource.data}
          ruleSetETag={resource.etag}
          onSaved={refresh}
          onClose={() => setManual(false)}
        />
      )}
      {rules.length > 0 && (
        <div className="batch-rule-toolbar">
          <span>仅勾选本次明确核对的待处理条款，最多500项。</span>
          <button
            className="button secondary"
            disabled={
              !current ||
              command.busy ||
              selectedPending.length === 0 ||
              !supportsApi(
                "POST",
                `/rule-sets/${resource.data?.id}/confirmations`,
              )
            }
            onClick={() => {
              if (!resource.data) return;
              void command
                .run<BatchRuleConfirmationView>(
                  `/rule-sets/${resource.data.id}/confirmations`,
                  {
                    items: selectedPending.map((rule) => ({
                      rule_id: rule.id,
                      expected_decision_revision: rule.decision_revision,
                    })),
                  },
                  resource.etag,
                )
                .then((result) => {
                  if (result) {
                    setBatchResult(result.data);
                    setSelected([]);
                    refresh();
                  }
                });
            }}
          >
            确认所选 {selectedPending.length} 项条款解释
          </button>
        </div>
      )}
      <ErrorNotice error={command.error} />
      {command.conflict && (
        <div className="notice warning">
          <span>
            规则包已变化，勾选内容保留。请刷新并重新核对，不自动套用旧确认。
          </span>
          <button className="button secondary" onClick={refresh}>
            刷新规则包
          </button>
        </div>
      )}
      {batchResult && (
        <div className="notice neutral" role="status">
          本次已按逐项结果记录，条款解释确认不代表正文满足。
          <ul>
            {batchResult.items.map((item) => (
              <li key={item.rule_id}>
                {item.rule_id.slice(0, 8)}：
                {item.status === "confirmed" ? "已确认" : "未覆盖原决定"}
              </li>
            ))}
          </ul>
        </div>
      )}
      {resource.data && rules.length === 0 && (
        <div className="panel small-empty">
          当前规则包尚未列出规则。这不表示招标文件没有要求或不存在风险。
        </div>
      )}
      {rules.map((rule) => (
        <article className="panel rule-card" key={rule.id}>
          <div className="rule-card-heading">
            <label className="rule-selection">
              <input
                type="checkbox"
                checked={selected.includes(rule.id)}
                disabled={
                  !current ||
                  rule.confirmation !== "pending" ||
                  (!selected.includes(rule.id) && selected.length >= 500)
                }
                onChange={(event) =>
                  setSelected((old) =>
                    event.target.checked
                      ? [...old, rule.id]
                      : old.filter((id) => id !== rule.id),
                  )
                }
                aria-label={`选择条款：${rule.statement}`}
              />
              <Badge
                tone={
                  rule.kind === "disqualification" || rule.kind === "mandatory"
                    ? "warning"
                    : ""
                }
              >
                {ruleKindNames[rule.kind]}
              </Badge>
            </label>
            <Badge>{confirmationNames[rule.confirmation]}</Badge>
          </div>
          <h3>{rule.statement}</h3>
          <div className="rule-qualifiers">
            <p>
              <strong>适用范围：</strong>
              {rule.scope.description}
            </p>
            <p>
              <strong>条件：</strong>
              {rule.conditions.length
                ? rule.conditions.join("；")
                : "未记录条件，请与原文核对"}
            </p>
            <p>
              <strong>例外：</strong>
              {rule.exceptions.length
                ? rule.exceptions.join("；")
                : "未记录例外，请与原文核对"}
            </p>
            {rule.maximum !== null && (
              <p>
                <strong>本条满分：</strong>
                {rule.maximum}
              </p>
            )}
          </div>
          {rule.origin_kind !== "model_extracted" && (
            <p className="manual-origin">
              人工补入来源保留
              {rule.provenance_status === "needs_revalidation"
                ? " · 来源效力或适用范围待重新核验"
                : rule.provenance_status === "superseded"
                  ? " · 已有据结束当前义务，历史保留"
                  : " · 当前来源有效"}
            </p>
          )}
          {rule.carryover && (
            <p className="small-note">
              沿用经等价核验的原决定，来源决定{" "}
              {rule.carryover.from_decision_id.slice(0, 8)}。
            </p>
          )}
          <EvidenceLinks ids={rule.evidence_ids} label="查看条款原文" />
          <DecisionForm
            kind="rules"
            id={rule.id}
            revision={rule.decision_revision}
            evidenceIds={rule.evidence_ids}
            onSaved={refresh}
          />
        </article>
      ))}
    </div>
  );
}
