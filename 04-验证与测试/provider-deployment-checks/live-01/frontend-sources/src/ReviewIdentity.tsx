import type { DocumentView, ReviewManifest } from "./contracts";
import { Badge } from "./components";
import { Evidence } from "./Evidence";
import { ResourceState } from "./ResourceState";
import { useResource } from "./useResource";
import { useCallback, useState } from "react";

export function ReviewIdentity({ reviewId }: { reviewId: string }) {
  const manifest = useResource<ReviewManifest>(`/reviews/${reviewId}/manifest`);
  const document = useResource<DocumentView>(
    manifest.data ? `/documents/${manifest.data.body_version_id}` : null,
  );
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  return (
    <div className="review-identity">
      <ResourceState
        {...manifest}
        hasData={!!manifest.data}
        onRetry={() => void manifest.reload()}
      />
      <ResourceState
        {...document}
        hasData={!!document.data}
        onRetry={() => void document.reload()}
      />
      {manifest.data && (
        <>
          <div className="review-body-identity">
            <span>本轮实际受检稿</span>
            <strong>
              {document.data
                ? `${document.data.filename} · 版本 ${document.data.file_revision}`
                : "正在读取固定稿件身份…"}
            </strong>
            {document.data && (
              <>
                <Badge>
                  {document.data.id === document.data.file_head_version_id
                    ? "此文件当前版本"
                    : "此文件历史版本"}
                </Badge>
                <button className="text-button" onClick={() => setOpen(true)}>
                  查看本轮受检原件
                </button>
              </>
            )}
          </div>
          <p className="small-note">
            本轮资料修订 {manifest.data.material_revision} · 依据有效时点{" "}
            {new Date(manifest.data.as_of).toLocaleString("zh-CN")}。
          </p>
          {!manifest.data.content_checks.consistency && (
            <p className="small-note">
              本轮未运行前后矛盾检测，已有未解决关键风险仍保留。
            </p>
          )}
          {!manifest.data.content_checks.scope_residue && (
            <p className="small-note">
              本轮未运行范围残留检测，已有未解决关键风险仍保留。
            </p>
          )}
          <details className="identity-details">
            <summary>本轮冻结身份与方法</summary>
            <dl>
              <dt>正文版本</dt>
              <dd>{manifest.data.body_version_id}</dd>
              <dt>生效资料快照</dt>
              <dd>{manifest.data.material_snapshot_id}</dd>
              <dt>规则包</dt>
              <dd>{manifest.data.rule_set_id}</dd>
              <dt>事实集</dt>
              <dd>{manifest.data.fact_set_id ?? "本轮没有已登记事实集"}</dd>
              <dt>方法身份</dt>
              <dd>{manifest.data.method_digest}</dd>
              <dt>实际冻结时刻</dt>
              <dd>
                {new Date(manifest.data.frozen_at).toLocaleString("zh-CN")}
              </dd>
            </dl>
          </details>
        </>
      )}
      {open && document.data && (
        <Evidence document={document.data} onClose={close} />
      )}
    </div>
  );
}
