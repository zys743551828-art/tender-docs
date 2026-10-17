import { useState } from "react";
import type {
  Bid,
  DocumentEvidenceView,
  MaterialView,
  RuleAddition,
  RuleSetView,
} from "./contracts";
import { Badge, ErrorNotice } from "./components";
import { ResourceState } from "./ResourceState";
import { useResource } from "./useResource";
import { useCommand } from "./useCommand";
import { ruleKindNames } from "./workspaceTypes";

const lines = (value: string) =>
  value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
export function ManualRuleForm({
  bid,
  ruleSet,
  ruleSetETag,
  onSaved,
  onClose,
}: {
  bid: Bid;
  ruleSet: RuleSetView;
  ruleSetETag: string | null;
  onSaved: () => void;
  onClose: () => void;
}) {
  const material = useResource<MaterialView>(`/bids/${bid.id}/materials`);
  const project = useResource<Bid>(`/bids/${bid.id}`);
  const [base, setBase] = useState({
    ruleSetId: ruleSet.id,
    materialRevision: ruleSet.material_revision,
  });
  const [documentId, setDocumentId] = useState("");
  const evidence = useResource<DocumentEvidenceView>(
    documentId ? `/documents/${documentId}/evidence` : null,
  );
  const [kind, setKind] = useState<RuleAddition["kind"]>("mandatory");
  const [statement, setStatement] = useState("");
  const [scope, setScope] = useState("");
  const [conditions, setConditions] = useState("");
  const [exceptions, setExceptions] = useState("");
  const [maximum, setMaximum] = useState("");
  const [bands, setBands] = useState<
    { label: string; description: string; points: string }[]
  >([]);
  const [quotes, setQuotes] = useState<
    { evidence_id: string; quote: string }[]
  >([]);
  const [reason, setReason] = useState("");
  const [saved, setSaved] = useState(false);
  const command = useCommand(`rule:manual:${bid.id}:${ruleSet.id}`);
  const stale =
    project.data &&
    (project.data.material_revision !== base.materialRevision ||
      ruleSet.id !== base.ruleSetId ||
      ruleSet.material_revision !== project.data.material_revision);
  async function submit() {
    const addition: RuleAddition = {
      kind,
      statement: statement.trim(),
      scope: { bid_id: bid.id, description: scope.trim() },
      conditions: lines(conditions),
      exceptions: lines(exceptions),
      maximum: kind === "scoring" ? maximum : null,
      bands:
        kind === "scoring"
          ? bands.map((band) => ({
              ...band,
              evidence_ids: quotes.map((quote) => quote.evidence_id),
            }))
          : [],
      evidence_quotes: quotes,
    };
    const result = await command.run<RuleSetView>(
      `/bids/${bid.id}/rule-set-corrections`,
      {
        base_rule_set_id: base.ruleSetId,
        reason: reason.trim(),
        additions: [addition],
      },
      ruleSetETag,
    );
    if (result) {
      setSaved(true);
      onSaved();
    }
  }
  return (
    <section className="panel manual-rule-panel">
      <div className="section-heading">
        <div>
          <h2>补充并确认条款</h2>
          <p className="muted">
            一次明确提交：新增规则包并记录此条款解释的人工确认。旧评审将过期，不代表正文满足，也不自动复评。
          </p>
        </div>
        <button className="text-button" onClick={onClose}>
          收起
        </button>
      </div>
      {saved ? (
        <div className="notice neutral" role="status">
          已新增并确认此条款。请查看新规则包，并在需要时按新包重新评审。
        </div>
      ) : (
        <form
          className="manual-rule-fields"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label>
            条款性质
            <select
              value={kind}
              onChange={(event) =>
                setKind(event.target.value as RuleAddition["kind"])
              }
            >
              {Object.entries(ruleKindNames).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            条款解释
            <textarea
              required
              rows={3}
              value={statement}
              onChange={(event) => setStatement(event.target.value)}
            />
          </label>
          <label>
            适用范围
            <input
              required
              value={scope}
              onChange={(event) => setScope(event.target.value)}
              placeholder="说明适用标段、对象或工程范围"
            />
          </label>
          <div className="field-pair">
            <label>
              适用条件（每行一项）
              <textarea
                rows={2}
                value={conditions}
                onChange={(event) => setConditions(event.target.value)}
              />
            </label>
            <label>
              例外（每行一项）
              <textarea
                rows={2}
                value={exceptions}
                onChange={(event) => setExceptions(event.target.value)}
              />
            </label>
          </div>
          {kind === "scoring" && (
            <div className="scoring-rule-fields">
              <label>
                条款满分
                <input
                  type="number"
                  min="0.0001"
                  step="any"
                  required
                  value={maximum}
                  onChange={(event) => setMaximum(event.target.value)}
                />
              </label>
              {bands.map((band, index) => (
                <div className="field-pair" key={index}>
                  <label>
                    分档名称
                    <input
                      required
                      value={band.label}
                      onChange={(event) =>
                        setBands((old) =>
                          old.map((item, i) =>
                            i === index
                              ? { ...item, label: event.target.value }
                              : item,
                          ),
                        )
                      }
                    />
                  </label>
                  <label>
                    分值
                    <input
                      type="number"
                      min="0"
                      step="any"
                      required
                      value={band.points}
                      onChange={(event) =>
                        setBands((old) =>
                          old.map((item, i) =>
                            i === index
                              ? { ...item, points: event.target.value }
                              : item,
                          ),
                        )
                      }
                    />
                  </label>
                  <label>
                    分档要求
                    <input
                      required
                      value={band.description}
                      onChange={(event) =>
                        setBands((old) =>
                          old.map((item, i) =>
                            i === index
                              ? { ...item, description: event.target.value }
                              : item,
                          ),
                        )
                      }
                    />
                  </label>
                </div>
              ))}
              <button
                type="button"
                className="text-button"
                onClick={() =>
                  setBands((old) => [
                    ...old,
                    { label: "", description: "", points: "" },
                  ])
                }
              >
                ＋ 添加原文评分分档
              </button>
            </div>
          )}
          <ResourceState
            {...material}
            hasData={!!material.data}
            onRetry={() => void material.reload()}
          />
          <label>
            招标／补遗来源
            <select
              value={documentId}
              onChange={(event) => setDocumentId(event.target.value)}
            >
              <option value="">选择已入库的来源文件</option>
              {material.data?.documents
                .filter(
                  (document) =>
                    document.role === "tender" ||
                    document.role === "supplement",
                )
                .map((document) => (
                  <option key={document.id} value={document.id}>
                    {document.filename} · 版本 {document.file_revision}
                  </option>
                ))}
            </select>
          </label>
          {documentId && (
            <>
              <ResourceState
                {...evidence}
                hasData={!!evidence.data}
                onRetry={() => void evidence.reload()}
              />
              {evidence.data?.gaps.map((gap, index) => (
                <p className="small-note" key={index}>
                  {gap}
                </p>
              ))}
              <div className="source-quote-picker">
                {evidence.data?.items.map((item) => (
                  <div key={item.id}>
                    <Badge>物理页 {item.physical_page}</Badge>
                    <p>{item.quote}</p>
                    <button
                      type="button"
                      className="text-button"
                      disabled={
                        quotes.some((quote) => quote.evidence_id === item.id) ||
                        quotes.length >= 50 ||
                        item.source_status === "withdrawn"
                      }
                      onClick={() =>
                        setQuotes((old) => [
                          ...old,
                          {
                            evidence_id: item.id,
                            quote: item.quote.slice(0, 8000),
                          },
                        ])
                      }
                    >
                      加入此页依据
                    </button>
                  </div>
                ))}
              </div>
            </>
          )}
          {quotes.map((quote, index) => (
            <label key={quote.evidence_id}>
              逐字依据 {index + 1}
              <textarea
                required
                rows={3}
                maxLength={8000}
                value={quote.quote}
                onChange={(event) =>
                  setQuotes((old) =>
                    old.map((item, i) =>
                      i === index
                        ? { ...item, quote: event.target.value }
                        : item,
                    ),
                  )
                }
              />
              <button
                type="button"
                className="text-button"
                onClick={() =>
                  setQuotes((old) =>
                    old.filter(
                      (item) => item.evidence_id !== quote.evidence_id,
                    ),
                  )
                }
              >
                移除此处依据
              </button>
            </label>
          ))}
          <label>
            补充原因
            <textarea
              rows={2}
              required
              maxLength={2000}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <p className="muted">
            逐字引用、来源效力及字段合法性均由服务端核验。失败时保留输入，不会部分确认或写入。
          </p>
          <ErrorNotice error={project.error ?? command.error} />
          {(stale || command.conflict) && (
            <div className="notice warning">
              <p>资料或规则包已变化，请保留输入并重新核对来源与当前规则包。</p>
              <button
                type="button"
                className="button secondary"
                onClick={() => {
                  void project.reload();
                  onSaved();
                }}
              >
                刷新当前资料与规则包
              </button>
              {(ruleSet.id !== base.ruleSetId ||
                ruleSet.material_revision !== base.materialRevision) && (
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => {
                    setBase({
                      ruleSetId: ruleSet.id,
                      materialRevision: ruleSet.material_revision,
                    });
                    command.clearError();
                  }}
                >
                  已查看当前规则包，保留输入继续核对
                </button>
              )}
            </div>
          )}
          <button
            className="button primary"
            disabled={
              command.busy ||
              !ruleSetETag ||
              !statement.trim() ||
              !scope.trim() ||
              !reason.trim() ||
              quotes.length === 0 ||
              !!stale
            }
          >
            补充并确认此条款
          </button>
        </form>
      )}
    </section>
  );
}
