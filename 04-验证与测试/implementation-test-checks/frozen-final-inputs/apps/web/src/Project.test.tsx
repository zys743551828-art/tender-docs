import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { Project } from "./Project";

function json(data: unknown, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), { status, headers });
}
const bid = {
  id: "bid-1",
  title: "滨河路工程",
  revision: 1,
  material_revision: 1,
  phase: "preparation",
};
const uploads = [
  {
    id: "upload-good",
    filename: "招标文件.pdf",
    state: "verified",
    binding_id: "binding-good",
    error_code: null,
  },
  {
    id: "upload-bad",
    filename: "补遗.pdf",
    state: "quarantined",
    binding_id: "binding-bad",
    error_code: "HASH_MISMATCH",
  },
];
const operation = { id: "op-1", state: "queued", kind: "materials.commit" };
let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>;
beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  localStorage.setItem("tender:batch:bid-1", "batch-1");
  fetchMock = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fetchMock);
});

it("确认请求响应丢失后重试复用幂等键，不创建第二次入库命令", async () => {
  const user = userEvent.setup();
  let attempts = 0;
  const keys: string[] = [];
  fetchMock.mockImplementation(async (url, options) => {
    if (url === "/api/v1/bids/bid-1")
      return json(bid, 200, { ETag: '"bid-1:r1"' });
    if (url === "/api/v1/bids/bid-1/materials")
      return json({ documents: [], conflicts: [] });
    if (url === "/api/v1/bids/bid-1/upload-batches/batch-1/commit") {
      attempts++;
      keys.push(
        (options?.headers as Record<string, string>)["Idempotency-Key"],
      );
      if (attempts === 1)
        throw new TypeError("response lost after server accepted");
      return json({ operation, resource_id: "material-1" }, 202);
    }
    return json({
      id: "batch-1",
      bid_id: "bid-1",
      state: attempts > 1 ? "committed" : "open",
      uploads: [uploads[0]],
    });
  });
  render(<Project id="bid-1" onBack={vi.fn()} />);
  await user.click(
    await screen.findByRole("button", { name: "确认 1 份资料入库" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "暂时无法连接服务",
  );
  await user.click(screen.getByRole("button", { name: "确认 1 份资料入库" }));
  expect(
    await screen.findByRole("heading", { name: "本批资料已入库" }),
  ).toBeVisible();
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBeTruthy();
  expect(keys[1]).toBe(keys[0]);
});

it("局部核验失败必须明确选择部分入库，不将失败补遗悄悄略过", async () => {
  const user = userEvent.setup();
  let committed = false;
  fetchMock.mockImplementation(async (url, options) => {
    if (url === "/api/v1/bids/bid-1")
      return json(bid, 200, { ETag: '"bid-1:r1"' });
    if (url === "/api/v1/bids/bid-1/materials")
      return json({ documents: [], conflicts: [], material_revision: 1 });
    if (url === "/api/v1/bids/bid-1/upload-batches/batch-1/commit") {
      committed = true;
      expect(options?.headers).toMatchObject({ "If-Match": '"bid-1:r1"' });
      expect(JSON.parse(options?.body as string)).toEqual({
        accepted_upload_ids: ["upload-good"],
        accepted_binding_ids: ["binding-good"],
        explicit_partial_acceptance: true,
      });
      return json({ operation, resource_id: "material-1" }, 202);
    }
    return json({
      id: "batch-1",
      bid_id: "bid-1",
      state: committed ? "committed" : "open",
      uploads,
    });
  });
  render(<Project id="bid-1" onBack={vi.fn()} />);
  const commit = await screen.findByRole("button", {
    name: "确认 1 份资料入库",
  });
  expect(commit).toBeDisabled();
  expect(screen.getByText(/核验未通过 · HASH_MISMATCH/)).toBeVisible();
  await user.click(
    screen.getByRole("checkbox", { name: /我确认仅将已核验的 1 份资料入库/ }),
  );
  await user.click(commit);
  expect(
    await screen.findByRole("heading", { name: "本批资料已入库" }),
  ).toBeVisible();
  expect(screen.getByText("已核验并入库")).toBeVisible();
  expect(
    fetchMock.mock.calls.filter(([, options]) => options?.method === "PUT"),
  ).toHaveLength(0);
});

it("资料包412冲突保留verified原件，刷新后由人重新确认且不重传", async () => {
  const user = userEvent.setup();
  let revision = 1;
  let attempts = 0;
  fetchMock.mockImplementation(async (url, options) => {
    if (url === "/api/v1/bids/bid-1")
      return json({ ...bid, revision }, 200, { ETag: `"bid-1:r${revision}"` });
    if (url === "/api/v1/bids/bid-1/materials")
      return json({ documents: [], conflicts: [], material_revision: 1 });
    if (url === "/api/v1/bids/bid-1/upload-batches/batch-1/commit") {
      attempts++;
      if (attempts === 1) {
        revision = 2;
        return json(
          { code: "REVISION_MISMATCH", message: "资料包已变化，请重新核对" },
          412,
        );
      }
      expect(options?.headers).toMatchObject({ "If-Match": '"bid-1:r2"' });
      return json({ operation, resource_id: "material-1" }, 202);
    }
    return json({
      id: "batch-1",
      bid_id: "bid-1",
      state: attempts > 1 ? "committed" : "open",
      uploads: [uploads[0]],
    });
  });
  render(<Project id="bid-1" onBack={vi.fn()} />);
  await user.click(
    await screen.findByRole("button", { name: "确认 1 份资料入库" }),
  );
  expect(await screen.findByText("资料版本已变化，请重新核对")).toBeVisible();
  expect(
    screen.getByRole("button", { name: "确认 1 份资料入库" }),
  ).toBeDisabled();
  expect(attempts).toBe(1);
  await user.click(screen.getByRole("button", { name: "刷新并查看当前资料" }));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "确认 1 份资料入库" }),
    ).toBeEnabled(),
  );
  expect(attempts).toBe(1);
  await user.click(screen.getByRole("button", { name: "确认 1 份资料入库" }));
  expect(
    await screen.findByRole("heading", { name: "本批资料已入库" }),
  ).toBeVisible();
  expect(attempts).toBe(2);
  expect(
    fetchMock.mock.calls.filter(([, options]) => options?.method === "PUT"),
  ).toHaveLength(0);
});
