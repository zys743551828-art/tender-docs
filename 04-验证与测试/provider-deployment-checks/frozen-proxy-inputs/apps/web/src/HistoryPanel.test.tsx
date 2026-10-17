import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { HistoryPanel } from "./HistoryPanel";
import type { DocumentView } from "./contracts";

const head: DocumentView = {
  id: "v2",
  bid_id: "bid-1",
  filename: "修改稿.pdf",
  role: "body",
  artifact_kind: "bid_body",
  sha256: "b".repeat(64),
  byte_size: 200,
  media_type: "application/pdf",
  obtained_at: "2026-10-16T12:00:00Z",
  parse_coverage: "partial",
  file_id: "file-1",
  file_revision: 2,
  previous_version_id: "v1",
  file_head_revision: 2,
  file_head_version_id: "v2",
  source_relation: null,
};
const older: DocumentView = {
  ...head,
  id: "v1",
  filename: "初稿.pdf",
  sha256: "a".repeat(64),
  byte_size: 100,
  file_revision: 1,
  previous_version_id: null,
};
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
  HTMLElement.prototype.scrollIntoView = vi.fn();
});

it("从真实版本链找回旧文件名和身份，打开旧原件且不套用当前检查", async () => {
  const user = userEvent.setup();
  const open = vi.fn();
  vi.mocked(fetch).mockResolvedValue(
    new Response(JSON.stringify(older), { status: 200 }),
  );
  render(<HistoryPanel head={head} onOpen={open} onClose={vi.fn()} />);
  expect(await screen.findByText("初稿.pdf")).toBeVisible();
  expect(screen.getByText("历史版本", { selector: "span" })).toBeVisible();
  await user.click(
    screen.getByRole("button", { name: "查看此历史原件与依据" }),
  );
  expect(open).toHaveBeenCalledWith(older);
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/documents/v1",
    expect.objectContaining({ method: "GET" }),
  );
  expect(
    screen.getByText("已到此文件的最早版本。历史版本不继承当前稿的检查结论。"),
  ).toBeVisible();
});

it("历史投影返回另一文件时拒绝展示，保留可重试错误", async () => {
  vi.mocked(fetch).mockResolvedValue(
    new Response(
      JSON.stringify({
        ...older,
        file_id: "wrong-file",
        filename: "不属于此链.pdf",
      }),
      { status: 200 },
    ),
  );
  render(<HistoryPanel head={head} onOpen={vi.fn()} onClose={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "历史文件身份不一致",
  );
  expect(screen.queryByText("不属于此链.pdf")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重试" })).toBeEnabled();
});
