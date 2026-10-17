import { useState } from "react";
import type { Bid, FactSetView, FactView } from "./contracts";
import { supportsApi } from "./api";
import { Badge, ErrorNotice } from "./components";
import { DecisionForm } from "./DecisionForm";
import { EvidenceLinks } from "./EvidenceLinks";
import { ResourceState } from "./ResourceState";
import { SourceSelection } from "./SourceSelection";
import { useCommand } from "./useCommand";
import { useResource } from "./useResource";
import { confirmationNames } from "./workspaceTypes";

export function FactsPanel({
  bid,
  onChanged,
}: {
  bid: Bid;
  onChanged: () => void;
}) {
  const resource = useResource<FactSetView>(`/bids/${bid.id}/facts`);
  const [editing, setEditing] = useState<FactView | "new" | null>(null);
  function refresh() {
    void resource.reload();
    onChanged();
  }
  return (
    <div>
      <section className="panel">
        <div className="section-heading">
          <div>
            <h2>工程事实</h2>
            <p className="muted">
              主体、单位、范围和来源分别核对；聊天或无依据断言不自动成为工程事实。
            </p>
          </div>
          <button
            className="button secondary"
            disabled={!supportsApi("POST", `/bids/${bid.id}/facts`)}
            onClick={() => setEditing("new")}
          >
            补充有据事实
          </button>
        </div>
        <ResourceState
          {...resource}
          hasData={!!resource.data}
          onRetry={() => void resource.reload()}
        />
        {resource.data?.facts.length === 0 && (
          <p className="small-empty">
            当前没有已登记工程事实。无需先建满事实库即可上传和开始适用范围的评审，相关缺口会单独列明。
          </p>
        )}
      </section>
      {editing && (
        <FactForm
          key={editing === "new" ? "new" : editing.id}
          bid={bid}
          fact={editing === "new" ? null : editing}
          onSaved={refresh}
          onClose={() => setEditing(null)}
        />
      )}
      {resource.data?.facts.map((fact) => (
        <article className="panel fact-card" key={fact.id}>
          <div className="section-heading">
            <div>
              <h3>{fact.name}</h3>
              <p className="muted">
                {fact.subject} · {fact.scope}
              </p>
            </div>
            <Badge>{confirmationNames[fact.confirmation]}</Badge>
          </div>
          <div className="fact-content">
            <p className="fact-value">
              {fact.value === null
                ? "未知／尚未明确"
                : typeof fact.value === "boolean"
                  ? fact.value
                    ? "是"
                    : "否"
                  : fact.value}
              {fact.value !== null && fact.unit ? ` ${fact.unit}` : ""}
            </p>
            <EvidenceLinks ids={fact.evidence_ids} label="查看事实来源" />
            <DecisionForm
              kind="facts"
              id={fact.id}
              revision={fact.decision_revision}
              evidenceIds={fact.evidence_ids}
              onSaved={refresh}
            />
            <div className="fact-maintenance">
              <button
                className="text-button"
                disabled={!supportsApi("POST", `/bids/${bid.id}/facts`)}
                onClick={() => setEditing(fact)}
              >
                以有据更正替换此事实
              </button>
              <FactWithdrawal bidId={bid.id} fact={fact} onSaved={refresh} />
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}

function FactForm({
  bid,
  fact,
  onSaved,
  onClose,
}: {
  bid: Bid;
  fact: FactView | null;
  onSaved: () => void;
  onClose: () => void;
}) {
  const project = useResource<FactSetView>(`/bids/${bid.id}/facts`);
  const [subject, setSubject] = useState(fact?.subject ?? "");
  const [scope, setScope] = useState(fact?.scope ?? "");
  const [name, setName] = useState(fact?.name ?? "");
  const [valueType, setValueType] = useState(
    fact?.value === null
      ? "unknown"
      : typeof fact?.value === "boolean"
        ? "boolean"
        : "text",
  );
  const [value, setValue] = useState(
    typeof fact?.value === "string" ? fact.value : "",
  );
  const [booleanValue, setBooleanValue] = useState(fact?.value === true);
  const [unit, setUnit] = useState(fact?.unit ?? "");
  const [evidenceIds, setEvidenceIds] = useState<string[]>(
    fact?.evidence_ids ?? [],
  );
  const [reason, setReason] = useState("");
  const [saved, setSaved] = useState(false);
  const command = useCommand(`fact:change:${bid.id}:${fact?.id ?? "new"}`);
  return (
    <section className="panel fact-form-panel">
      <div className="section-heading">
        <div>
          <h2>{fact ? "有据更正工程事实" : "补充工程事实"}</h2>
          <p className="muted">
            建立新事实集并保留旧依据。相关旧评审会标过期，由你决定何时复评。
          </p>
        </div>
        <button className="text-button" onClick={onClose}>
          收起
        </button>
      </div>
      {saved ? (
        <div className="notice neutral" role="status">
          已保存新事实集，旧记录与来源保留。请按当前资料核对评审是否需要更新。
        </div>
      ) : (
        <form
          className="fact-form"
          onSubmit={(event) => {
            event.preventDefault();
            const input = {
              subject: subject.trim(),
              scope: scope.trim(),
              name: name.trim(),
              value:
                valueType === "unknown"
                  ? null
                  : valueType === "boolean"
                    ? booleanValue
                    : value,
              unit: unit.trim() || null,
              evidence_ids: evidenceIds,
              reason: reason.trim(),
            };
            void command
              .run<FactSetView>(
                `/bids/${bid.id}/facts`,
                { fact: input, replaces_fact_id: fact?.id ?? null },
                project.etag,
              )
              .then((result) => {
                if (result) {
                  setSaved(true);
                  onSaved();
                }
              });
          }}
        >
          <div className="field-pair">
            <label>
              事实主体
              <input
                required
                value={subject}
                onChange={(event) => setSubject(event.target.value)}
              />
            </label>
            <label>
              适用范围
              <input
                required
                value={scope}
                onChange={(event) => setScope(event.target.value)}
              />
            </label>
          </div>
          <label>
            事实名称
            <input
              required
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label>
            取值类型
            <select
              value={valueType}
              onChange={(event) => setValueType(event.target.value)}
            >
              <option value="text">文字／数值</option>
              <option value="boolean">是／否</option>
              <option value="unknown">未知</option>
            </select>
          </label>
          {valueType === "text" && (
            <label>
              事实取值
              <input
                required
                value={value}
                onChange={(event) => setValue(event.target.value)}
              />
            </label>
          )}
          {valueType === "boolean" && (
            <label>
              事实取值
              <select
                value={String(booleanValue)}
                onChange={(event) =>
                  setBooleanValue(event.target.value === "true")
                }
              >
                <option value="true">是</option>
                <option value="false">否</option>
              </select>
            </label>
          )}
          <label>
            单位（如适用）
            <input
              value={unit}
              onChange={(event) => setUnit(event.target.value)}
              placeholder="保留原单位，不自动换算或省略"
            />
          </label>
          <SourceSelection
            bidId={bid.id}
            selected={evidenceIds}
            onChange={setEvidenceIds}
          />
          <label>
            依据与变更原因
            <textarea
              required
              rows={2}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <ErrorNotice error={project.error ?? command.error} />
          {command.conflict && (
            <div className="notice warning">
              <p>项目或事实已变化，输入保留；请刷新后重新核对。</p>
              <button
                type="button"
                className="button secondary"
                onClick={() => {
                  void project.reload();
                  onSaved();
                }}
              >
                刷新当前事实与项目
              </button>
            </div>
          )}
          <button
            className="button primary"
            disabled={command.busy || !project.etag || evidenceIds.length === 0}
          >
            {fact ? "保存并确认有据更正" : "保存并确认事实"}
          </button>
        </form>
      )}
    </section>
  );
}

function FactWithdrawal({
  bidId,
  fact,
  onSaved,
}: {
  bidId: string;
  fact: FactView;
  onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const resource = useResource<FactSetView>(
    open ? `/bids/${bidId}/facts` : null,
  );
  const command = useCommand(`fact:withdraw:${fact.id}`);
  return (
    <div>
      <button
        className="text-button"
        aria-expanded={open}
        disabled={
          !supportsApi("POST", `/facts/${fact.id}/withdrawals`) ||
          !supportsApi("GET", `/bids/${bidId}/facts`)
        }
        onClick={() => setOpen(!open)}
      >
        撤回当前事实
      </button>
      {open && (
        <form
          className="decision-form"
          onSubmit={(event) => {
            event.preventDefault();
            void command
              .run<FactSetView>(
                `/facts/${fact.id}/withdrawals`,
                { reason: reason.trim() },
                resource.etag,
              )
              .then((result) => {
                if (result) {
                  setOpen(false);
                  onSaved();
                }
              });
          }}
        >
          <p className="muted">
            撤回将建立新事实集并保留历史；旧评审增加影响提示，不删除原始报告。
          </p>
          <label>
            撤回依据
            <textarea
              required
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              rows={2}
            />
          </label>
          <ErrorNotice error={resource.error ?? command.error} />
          {command.conflict && (
            <button
              className="text-button"
              type="button"
              onClick={() => {
                void resource.reload();
                onSaved();
              }}
            >
              对象已变化，刷新后核对
            </button>
          )}
          <button
            className="button secondary"
            disabled={
              !reason.trim() ||
              command.busy ||
              !resource.etag ||
              !resource.data?.facts.some((item) => item.id === fact.id)
            }
          >
            确认撤回此事实
          </button>
        </form>
      )}
    </div>
  );
}
