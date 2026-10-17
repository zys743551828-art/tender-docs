import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { ApiError, api } from "./api";
import { Evidence } from "./Evidence";
import type { DocumentView } from "./contracts";

vi.mock("./PdfPreview", () => ({
  PdfPreview: ({ filename, page }: { filename: string; page: number }) => (
    <canvas
      role="img"
      aria-label={`${filename}，物理页${page}原页`}
      data-page={page}
    />
  ),
}));

const auth = {
  member: { id: "member-1", display_name: "张工", active: true },
  csrf_token: "csrf-test",
  expires_at: "2026-12-01T00:00:00Z",
};
function json(data: unknown, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}
let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>;
beforeEach(() => {
  history.replaceState({}, "", "/projects");
  localStorage.clear();
  fetchMock = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fetchMock);
});

describe("投标工作区", () => {
  it("登录失败持续显示，成功后空工作区不展示模拟指标", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url) => {
      if (url === "/api/v1/auth/session")
        return json({ code: "UNAUTHENTICATED", message: "请登录" }, 401);
      if (url === "/api/v1/auth/login")
        return json(
          { code: "INVALID_CREDENTIALS", message: "账号或密码不正确" },
          401,
        );
      return json({ items: [], next_cursor: null, has_more: false });
    });
    render(<App />);
    await user.type(await screen.findByLabelText("账号"), "zhang");
    await user.type(screen.getByLabelText("密码"), "invalid");
    await user.click(screen.getByRole("button", { name: "登录工作区" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "账号或密码不正确",
    );
    expect(screen.getByLabelText("账号")).toHaveValue("zhang");
    fetchMock.mockImplementation(async (url) =>
      url === "/api/v1/auth/login"
        ? json(auth)
        : json({ items: [], next_cursor: null, has_more: false }),
    );
    await user.click(screen.getByRole("button", { name: "登录工作区" }));
    expect(await screen.findByText("从一份资料，开始一个项目")).toBeVisible();
    expect(screen.queryByText(/中标率|收入|87分/)).not.toBeInTheDocument();
  });

  it("会话过期保留未提交名称；重新登录不自动重放写入", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url, init) => {
      if (url === "/api/v1/auth/session" || url === "/api/v1/auth/login")
        return json(auth);
      if (url === "/api/v1/bids" && init?.method === "POST")
        return json({ code: "SESSION_EXPIRED", message: "请重新登录" }, 401);
      return json({ items: [], next_cursor: null, has_more: false });
    });
    render(<App />);
    await user.click(
      await screen.findByRole("button", { name: "＋ 新建投标" }),
    );
    await user.type(screen.getByLabelText("项目名称"), "滨河路工程");
    await user.click(screen.getByRole("button", { name: "创建并上传资料" }));
    expect(
      await screen.findByRole("heading", { name: "登录已过期" }),
    ).toBeVisible();
    await user.type(screen.getByLabelText("账号"), "zhang");
    await user.type(screen.getByLabelText("密码"), "correct");
    await user.click(screen.getByRole("button", { name: "登录工作区" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("heading", { name: "登录已过期" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByLabelText("项目名称")).toHaveValue("滨河路工程");
    const writes = fetchMock.mock.calls.filter(
      ([url, options]) => url === "/api/v1/bids" && options?.method === "POST",
    );
    expect(writes).toHaveLength(1);
    const headers = writes[0][1]?.headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("csrf-test");
    expect(headers["Idempotency-Key"]).toBeTruthy();
  });

  it("网络失败返回可恢复错误，不伪造业务结果", async () => {
    fetchMock.mockRejectedValue(new TypeError("network unavailable"));
    await expect(api("/bids")).rejects.toMatchObject({
      code: "NETWORK_ERROR",
      retryable: true,
      status: 0,
    } satisfies Partial<ApiError>);
  });
});

describe("解析依据", () => {
  const document: DocumentView = {
    id: "doc-1",
    bid_id: "bid-1",
    filename: "招标文件.pdf",
    role: "tender",
    artifact_kind: "source_original",
    sha256: "a".repeat(64),
    byte_size: 1024,
    media_type: "application/pdf",
    obtained_at: "2026-10-16T10:00:00Z",
    parse_coverage: "partial",
    file_id: "file-1",
    file_revision: 1,
    previous_version_id: null,
    file_head_revision: 1,
    file_head_version_id: "doc-1",
    source_relation: null,
  };
  it("显示扫描缺口、真实物理页及原件用途，关闭后恢复焦点", async () => {
    const onClose = vi.fn();
    const original = window.document.createElement("button");
    original.textContent = "查看原件与依据";
    window.document.body.appendChild(original);
    original.focus();
    fetchMock.mockImplementation(async (url) =>
      url === "/api/v1/documents/doc-1/evidence"
        ? json({
            document_version_id: "doc-1",
            parse_run_id: "parse-1",
            coverage: "partial",
            gaps: ["物理页2为扫描页，OCR未配置"],
            items: [
              {
                id: "ev-1",
                document_version_id: "doc-1",
                physical_page: 1,
                printed_label: null,
                quote: "投标文件须加盖公章",
                kind: "text",
                source_status: "published",
              },
              {
                id: "ev-3",
                document_version_id: "doc-1",
                physical_page: 3,
                printed_label: "2",
                quote: "工期要求90日历天",
                kind: "text",
                source_status: "published",
              },
            ],
          })
        : json({
            artifact_id: "doc-1",
            download_url: "http://127.0.0.1:19000/private/real-signed-url",
            expires_at: "2026-10-16T14:00:00Z",
            filename: "招标文件.pdf",
            artifact_kind: "source_original",
            presentation_revision: null,
          }),
    );
    const view = render(<Evidence document={document} onClose={onClose} />);
    const dialog = screen.getByRole("dialog");
    expect(
      await within(dialog).findByText("物理页2为扫描页，OCR未配置"),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "下载资料原件" })).toBeEnabled();
    expect(screen.getByText("投标文件须加盖公章")).toBeVisible();
    fireEvent.change(screen.getByRole("combobox", { name: "物理页" }), {
      target: { value: "3" },
    });
    expect(screen.getByText("工期要求90日历天")).toBeVisible();
    expect(
      screen.getByRole("img", { name: "招标文件.pdf，物理页3原页" }),
    ).toHaveAttribute("data-page", "3");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
    view.unmount();
    expect(original).toHaveFocus();
    original.remove();
  });
});
