import { useState } from "react";
import type { DocumentEvidenceView, MaterialView } from "./contracts";
import { Badge } from "./components";
import { ResourceState } from "./ResourceState";
import { useResource } from "./useResource";

export function SourceSelection({
  bidId,
  selected,
  onChange,
}: {
  bidId: string;
  selected: string[];
  onChange: (ids: string[]) => void;
}) {
  const materials = useResource<MaterialView>(`/bids/${bidId}/materials`);
  const [documentId, setDocumentId] = useState("");
  const evidence = useResource<DocumentEvidenceView>(
    documentId ? `/documents/${documentId}/evidence` : null,
  );
  return (
    <div className="source-selection">
      <ResourceState
        {...materials}
        hasData={!!materials.data}
        onRetry={() => void materials.reload()}
      />
      <label>
        选择来源文件
        <select
          value={documentId}
          onChange={(event) => setDocumentId(event.target.value)}
        >
          <option value="">选择已入库资料</option>
          {materials.data?.documents
            .filter(
              (document) =>
                document.role !== "official_result" && document.role !== "body",
            )
            .map((document) => (
              <option value={document.id} key={document.id}>
                {document.filename} · 版本 {document.file_revision}
              </option>
            ))}
        </select>
      </label>
      <p className="small-note">
        仅选择能证明该项工程事实的来源。已选 {selected.length}{" "}
        处依据，最终效力与适用性由服务端核验。
      </p>
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
              <label key={item.id} className="evidence-select-item">
                <input
                  type="checkbox"
                  checked={selected.includes(item.id)}
                  disabled={
                    item.source_status === "withdrawn" ||
                    (!selected.includes(item.id) && selected.length >= 100)
                  }
                  onChange={(event) =>
                    onChange(
                      event.target.checked
                        ? [...selected, item.id]
                        : selected.filter((id) => id !== item.id),
                    )
                  }
                />
                <span>
                  <Badge>物理页 {item.physical_page}</Badge>
                  <span className="source-quote-text">{item.quote}</span>
                </span>
              </label>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
