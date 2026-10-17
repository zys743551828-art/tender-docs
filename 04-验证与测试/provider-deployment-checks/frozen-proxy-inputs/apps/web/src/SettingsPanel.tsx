import { useEffect, useState } from "react";
import type { ProjectSettings, SettingsView } from "./contracts";
import { ErrorNotice } from "./components";
import { ResourceState } from "./ResourceState";
import { useCommand } from "./useCommand";
import { useResource } from "./useResource";

const labels: [keyof ProjectSettings, string, string][] = [
  [
    "auto_review",
    "上传后自动开始评审",
    "关闭只停止无人发起的自动衔接，仍可明确手动开始。",
  ],
  [
    "consistency",
    "前后矛盾检测",
    "关闭后停止新检测；先前发现且尚未解决的关键风险仍保留。",
  ],
  [
    "scope_residue",
    "范围残留检测",
    "关闭不隐藏先前关键风险，也不会自动解决问题。",
  ],
  [
    "show_prediction",
    "显示预测数值",
    "关闭时隐藏预测数值与区间，仍展示风险、覆盖、逐项缺口和不可估分原因。",
  ],
  ["pricing", "报价辅助", "只影响可选报价模块，不改变核心风险核对和提交条件。"],
  ["result_reminder", "结果提醒", "关闭催办不影响补录或更正正式结果。"],
  [
    "accept_rules_as_written",
    "规则按原文接受",
    "明确启用才影响默认规则确认条件；报告仍显示未人工确认的依据。",
  ],
];
export function SettingsPanel({
  bidId,
  onSaved,
}: {
  bidId: string;
  onSaved: () => void;
}) {
  const resource = useResource<SettingsView>(`/bids/${bidId}/settings`);
  const [draft, setDraft] = useState<ProjectSettings | null>(null);
  const [base, setBase] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState(false);
  const command = useCommand(`settings:${bidId}`);
  useEffect(() => {
    if (resource.data && !dirty) {
      setDraft(resource.data.settings);
      setBase(resource.data.revision);
    }
  }, [resource.data, dirty]);
  const conflict = resource.data && base !== resource.data.revision;
  return (
    <section className="panel settings-panel">
      <div className="section-heading">
        <div>
          <h2>项目设置</h2>
          <p className="muted">
            仅修改当前项目。核心否决核对、响应矩阵、证据和提交条件没有关闭入口。
          </p>
        </div>
      </div>
      <ResourceState
        {...resource}
        hasData={!!resource.data}
        onRetry={() => void resource.reload()}
      />
      {draft && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void command
              .run<SettingsView>(
                `/bids/${bidId}/settings`,
                draft,
                resource.etag,
                "PATCH",
              )
              .then((result) => {
                if (result) {
                  setDirty(false);
                  setSaved(true);
                  void resource.reload();
                  onSaved();
                }
              });
          }}
        >
          <div className="settings-list">
            {labels.map(([key, label, description]) => (
              <label className="setting-row" key={key}>
                <span>
                  <strong>{label}</strong>
                  <small>{description}</small>
                </span>
                <input
                  type="checkbox"
                  role="switch"
                  checked={draft[key]}
                  onChange={(event) => {
                    setDraft({ ...draft, [key]: event.target.checked });
                    setDirty(true);
                    setSaved(false);
                  }}
                />
              </label>
            ))}
          </div>
          <div className="settings-footer">
            <ErrorNotice error={command.error} />
            {(conflict || command.conflict) && (
              <div className="notice warning">
                <div>
                  <strong>设置已被其他操作更新</strong>
                  <p>
                    当前输入保留。先刷新查看服务端设置；采用当前设置后再编辑，避免旧卡覆盖其他变更。
                  </p>
                  <button
                    type="button"
                    className="button secondary"
                    onClick={() => void resource.reload()}
                  >
                    刷新当前设置
                  </button>
                  {conflict && (
                    <button
                      type="button"
                      className="button secondary"
                      onClick={() => {
                        setDirty(false);
                        command.clearError();
                      }}
                    >
                      采用当前设置，重新编辑
                    </button>
                  )}
                </div>
              </div>
            )}
            {saved && (
              <p className="inline-success" role="status">
                设置已保存；已有关键风险和历史报告保留。
              </p>
            )}
            <button
              className="button primary"
              disabled={!dirty || command.busy || !!conflict || !resource.etag}
            >
              保存当前项目设置
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
