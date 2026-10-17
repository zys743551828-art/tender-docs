import { useState } from "react";
import { supportsApi } from "./api";
import type {
  DecisionView,
  RuleView,
  FactView,
  FindingView,
} from "./contracts";
import { ErrorNotice } from "./components";
import { useCommand } from "./useCommand";
import { useResource } from "./useResource";

const choices = {
  rules: [
    ["confirmed", "确认条款解释"],
    ["rejected", "驳回此条款解释"],
    ["deferred", "暂缓核对"],
  ],
  facts: [
    ["confirmed", "确认工程事实"],
    ["rejected", "驳回此项事实"],
  ],
  findings: [
    ["confirm", "确认待改"],
    ["dismiss", "有理由驳回误报"],
    ["defer", "暂缓处理"],
    ["reopen", "重新打开问题"],
  ],
} as const;

export function DecisionForm({
  kind,
  id,
  revision,
  evidenceIds,
  onSaved,
}: {
  kind: keyof typeof choices;
  id: string;
  revision: number;
  evidenceIds: string[];
  onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState<string>(choices[kind][0][0]);
  const [reason, setReason] = useState("");
  const [saved, setSaved] = useState(false);
  const resource = useResource<RuleView | FactView | FindingView>(
    open ? `/${kind}/${id}` : null,
  );
  const command = useCommand(`decision:${kind}:${id}`);
  const available =
    supportsApi("GET", `/${kind}/${id}`) &&
    supportsApi("POST", `/${kind}/${id}/decisions`);
  const stale = resource.data && resource.data.decision_revision !== revision;
  async function submit() {
    const result = await command.run<DecisionView>(
      `/${kind}/${id}/decisions`,
      { decision: choice, reason: reason.trim(), evidence_ids: evidenceIds },
      resource.etag,
    );
    if (result) {
      setSaved(true);
      setOpen(false);
      onSaved();
    }
  }
  return (
    <div className="decision-control">
      <button
        className="text-button"
        disabled={!available}
        aria-expanded={open}
        onClick={() => {
          setOpen(!open);
          setSaved(false);
        }}
      >
        处理本条
      </button>
      {!available && <small>人工决定服务尚未开放</small>}
      {saved && (
        <p className="inline-success" role="status">
          决定已记录。
          {kind === "rules"
            ? "条款确认不代表正文已满足。"
            : kind === "findings"
              ? "有效新稿复检完成后才能判断问题是否解决。"
              : "以本次确认的来源与范围为准。"}
        </p>
      )}
      {open && (
        <form
          className="decision-form"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label>
            处理方式
            <select
              value={choice}
              onChange={(event) => setChoice(event.target.value)}
            >
              {choices[kind].map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            依据与原因
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              required
              rows={2}
              placeholder={
                kind === "findings"
                  ? "说明处理依据；暂缓仍保留待处理风险"
                  : "说明对所列原文、条件和来源的核对结果"
              }
            />
          </label>
          {kind === "findings" && (
            <p className="muted">
              确认待改、采纳建议或表示已修改均不会手动解决问题；请上传新稿进行有效复检。
            </p>
          )}
          <ErrorNotice error={resource.error ?? command.error} />
          {(command.conflict || stale) && (
            <div className="notice warning">
              <p>
                本条记录已变化，输入已保留。请刷新并重新核对对象，再明确提交。
              </p>
              <button
                type="button"
                className="button secondary"
                onClick={() => {
                  void resource.reload();
                  onSaved();
                }}
              >
                刷新本条记录
              </button>
            </div>
          )}
          <button
            className="button primary"
            disabled={
              command.busy ||
              resource.loading ||
              !resource.etag ||
              !reason.trim() ||
              !!stale
            }
            type="submit"
          >
            {command.busy
              ? "正在记录…"
              : choices[kind].find(([value]) => value === choice)?.[1]}
          </button>
        </form>
      )}
    </div>
  );
}
