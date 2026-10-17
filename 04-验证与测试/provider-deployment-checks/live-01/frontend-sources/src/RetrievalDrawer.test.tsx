import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { RetrievalDrawer } from "./RetrievalDrawer";
import type { DocumentView, RetrievalResult } from "./contracts";

// Explicit component fixtures: these do not claim the retrieval API is deployed.
const capability = vi.hoisted(() => ({ retrieval: true }));
vi.mock("./api", async (original) => {
  const actual = await original<typeof import("./api")>();
  return {
    ...actual,
    supportsApi: (method: string, path: string) =>
      path === "/retrievals"
        ? capability.retrieval
        : actual.supportsApi(method, path),
  };
});
vi.mock("./Evidence", () => ({
  Evidence: ({
    document,
    initialPage,
    contextNotice,
    onClose,
  }: {
    document: DocumentView;
    initialPage: number;
    contextNotice?: string;
    onClose: () => void;
  }) => (
    <div role="dialog" aria-label="测试原页">
      <span>
        {document.id} · 物理页{initialPage}
      </span>
      {contextNotice && <p>{contextNotice}</p>}
      <button onClick={onClose}>关闭测试原页</button>
    </div>
  ),
}));
const document: DocumentView = {
  id: "source-v1",
  bid_id: "bid-1",
  filename: "工期要求.pdf",
  role: "tender",
  artifact_kind: "source_original",
  sha256: "a".repeat(64),
  byte_size: 700,
  media_type: "application/pdf",
  obtained_at: "2026-10-16T00:00:00Z",
  parse_coverage: "partial",
  file_id: "file-1",
  file_revision: 1,
  previous_version_id: null,
  file_head_revision: 1,
  file_head_version_id: "source-v1",
  source_relation: null,
};
const result: RetrievalResult = {
  trace_id: "trace-1",
  policy_digest: "policy-test",
  coverage: "partial",
  gaps: ["第2页扫描范围尚未建立文字索引"],
  hits: [
    {
      chunk_id: "chunk-1",
      source_version_id: document.id,
      parse_run_id: "parse-1",
      physical_page: 3,
      citation_id: "citation-is-not-evidence",
      excerpt: "总工期240日历天，雨季条件另见附件。",
      embedding_profile_id: "local-profile",
      rank: 1,
    },
  ],
};
function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
let transport: ReturnType<typeof vi.fn<typeof fetch>>;
beforeEach(() => {
  capability.retrieval = true;
  sessionStorage.clear();
  transport = vi
    .fn<typeof fetch>()
    .mockImplementation(async (url) =>
      url === "/api/v1/retrievals" ? json(result) : json(document),
    );
  vi.stubGlobal("fetch", transport);
});
function open() {
  render(<RetrievalDrawer bidId="bid-1" title="合成项目" onClose={vi.fn()} />);
}
it("未注册查证API保留问题草稿，不用空结果冒充成功", async () => {
  capability.retrieval = false;
  open();
  await userEvent.type(
    screen.getByRole("textbox", { name: "想查证什么？" }),
    "工期有什么要求",
  );
  expect(
    screen.getByRole("button", { name: "查证当前项目资料" }),
  ).toBeDisabled();
  expect(screen.getByText("资料查证服务尚未开放")).toBeVisible();
  expect(sessionStorage.getItem("tender:retrieval-draft:bid-1")).toBe(
    "工期有什么要求",
  );
  expect(transport).not.toHaveBeenCalled();
});
it("展示实际索引缺口和摘录，引用先验证原检索后打开精确版本物理页", async () => {
  open();
  await userEvent.type(screen.getByRole("textbox"), "工期");
  await userEvent.click(
    screen.getByRole("button", { name: "查证当前项目资料" }),
  );
  expect(
    await screen.findByText("第2页扫描范围尚未建立文字索引"),
  ).toBeVisible();
  expect(await screen.findByText(document.filename)).toBeVisible();
  expect(screen.getByText("检索范围不完整")).toBeVisible();
  await userEvent.click(
    screen.getByRole("button", { name: "核对来源并打开引用原页" }),
  );
  expect(
    await screen.findByRole("dialog", { name: "测试原页" }),
  ).toHaveTextContent("source-v1 · 物理页3");
  const calls = transport.mock.calls.filter(
    ([url]) => url === "/api/v1/retrievals",
  );
  expect(calls).toHaveLength(2);
  expect(calls[1][1]?.body).toBe(calls[0][1]?.body);
  expect(new Headers(calls[1][1]?.headers).get("Idempotency-Key")).toBe(
    new Headers(calls[0][1]?.headers).get("Idempotency-Key"),
  );
  expect(
    transport.mock.calls.some(([url]) =>
      String(url).includes("/evidence/citation-is-not-evidence"),
    ),
  ).toBe(false);
});
it("网络失败后重试原输入与同一命令键，修改中的问题不悄悄替换失败命令", async () => {
  let retrievals = 0;
  transport.mockImplementation(async (url) => {
    if (url !== "/api/v1/retrievals") return json(document);
    if (++retrievals === 1) throw new TypeError("offline");
    return json(result);
  });
  open();
  const input = screen.getByRole("textbox");
  await userEvent.type(input, "原问题");
  await userEvent.click(
    screen.getByRole("button", { name: "查证当前项目资料" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "暂时无法连接服务",
  );
  await userEvent.clear(input);
  await userEvent.type(input, "修改后问题");
  await userEvent.click(screen.getByRole("button", { name: "重试" }));
  expect(await screen.findByText("本次查证：原问题")).toBeVisible();
  const calls = transport.mock.calls.filter(
    ([url]) => url === "/api/v1/retrievals",
  );
  expect(calls[1][1]?.body).toBe(calls[0][1]?.body);
  expect(new Headers(calls[1][1]?.headers).get("Idempotency-Key")).toBe(
    new Headers(calls[0][1]?.headers).get("Idempotency-Key"),
  );
  expect(input).toHaveValue("修改后问题");
  await userEvent.click(
    screen.getByRole("button", { name: "查证当前项目资料" }),
  );
  expect(await screen.findByText("本次查证：修改后问题")).toBeVisible();
});
it("引用来源变化拒绝当作当前依据，明确历史入口保留原版本与页码", async () => {
  let retrievals = 0;
  transport.mockImplementation(async (url) => {
    if (url !== "/api/v1/retrievals") return json(document);
    return ++retrievals === 1
      ? json(result)
      : json(
          {
            code: "RETRIEVAL_SOURCE_CHANGED",
            message: "此前检索依据已变化，请重新查证",
          },
          409,
        );
  });
  open();
  await userEvent.type(screen.getByRole("textbox"), "工期");
  await userEvent.click(
    screen.getByRole("button", { name: "查证当前项目资料" }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "核对来源并打开引用原页" }),
  );
  expect(await screen.findByText("本次检索依据已变化")).toBeVisible();
  expect(
    screen.queryByRole("dialog", { name: "测试原页" }),
  ).not.toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: "查看当时原页（历史）" }),
  );
  expect(
    await screen.findByRole("dialog", { name: "测试原页" }),
  ).toHaveTextContent("正在追溯当时的检索原页");
  expect(screen.getByRole("dialog", { name: "测试原页" })).toHaveTextContent(
    "source-v1 · 物理页3",
  );
  expect(retrievals).toBe(2);
});
it("空命中与服务失败均不能显示为已核对无风险", async () => {
  transport.mockResolvedValueOnce(
    json({
      ...result,
      hits: [],
      coverage: "unavailable",
      gaps: ["当前资料尚无可用索引"],
    }),
  );
  open();
  await userEvent.type(screen.getByRole("textbox"), "工期");
  await userEvent.click(
    screen.getByRole("button", { name: "查证当前项目资料" }),
  );
  expect(await screen.findByText(/本次没有返回可用摘录/)).toBeVisible();
  expect(screen.getByText("当前检索范围不可用")).toBeVisible();
  transport.mockResolvedValueOnce(
    json(
      { code: "RAG_UNAVAILABLE", message: "检索模型服务暂不可用，请稍后重试" },
      503,
    ),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "查证当前项目资料" }),
  );
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent("检索模型服务暂不可用"),
  );
  expect(screen.getByText(/本次没有返回可用摘录/)).toBeVisible();
  expect(screen.queryByText("核对通过")).not.toBeInTheDocument();
});
