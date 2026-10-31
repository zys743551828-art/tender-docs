import React from "C:/Users/74355/Documents/Codex/2026-10-30/task/tender-app-dev/workbench/apps/web/node_modules/react/index.js";
import {createRoot} from "C:/Users/74355/Documents/Codex/2026-10-30/task/tender-app-dev/workbench/apps/web/node_modules/react-dom/client.js";
import {WordCard} from "C:/Users/74355/Documents/Codex/2026-10-30/task/tender-app-dev/workbench/apps/web/src/WordOriginals.tsx";
import "C:/Users/74355/Documents/Codex/2026-10-30/task/tender-app-dev/workbench/apps/web/src/styles.css";
const word = {
  id: "word",
  bid_id: "bid",
  file_id: "file",
  original_name: "修改稿.docx",
  role: "body",
  original_saved: true,
  source_sha256: "a".repeat(64),
  byte_size: 100,
  inspection: {
    schema: "word-intake-inspection/1",
    container_validated: true,
    entry_count: 2,
    expanded_bytes: 100,
    xml_part_count: 1,
    active_content_gaps: [],
    unsupported_layout_features: [],
    requested_fonts: [],
    conversion_eligible: true,
    text_digest: "b".repeat(64),
  },
  conversion: {
    state: "succeeded",
    operation_id: "op",
    conversion_attempted: true,
    coverage: "partial",
    error_code: null,
    retryable: true,
    gaps: ["某字体尚未核验"],
    pdf_document_version_id: "pdf",
    controlled_render_verified: false,
  },
  message: "原件保留，转换范围尚未完整",
  delivery_check_message: "Word交付未获对应检查资格",
  review_qualification: "not_established_by_conversion",
  primary_action: {
    action: "upload_modified_pdf",
    label: "上传此修改稿的PDF",
    context: {
      bid_id: "bid",
      role: "body",
      declared_source_document_version_id: "word",
      target: { kind: "new_file" },
    },
  },
  converted_pdf_action: { action: "open_pdf", document_version_id: "pdf" },
};

const captured={"id": "76796728-4d86-45c5-aa81-ea42e259995b", "original_name": "合成正常实例Word.docx", "conversion": {"state": "failed", "coverage": "pending", "gaps": ["WORD_CONVERSION_RESULT_MISSING"], "error_code": null, "retryable": true, "conversion_attempted": false, "operation_id": "7ad18b9e-c77f-4470-bc8d-2ae4211c0080"}};
const failed={...word,...captured,conversion:{...word.conversion,...captured.conversion,pdf_document_version_id:null,controlled_render_verified:false},converted_pdf_action:null};
window.fetch=async()=>new Response(JSON.stringify(failed),{status:200,headers:{"Content-Type":"application/json"}});
createRoot(document.getElementById("root")).render(<main style={{maxWidth:1100,margin:"24px auto",padding:16}}><h1>Word原件</h1><WordCard initial={failed} bidId="bid" disabled={false} onPdf={()=>{}} /></main>);