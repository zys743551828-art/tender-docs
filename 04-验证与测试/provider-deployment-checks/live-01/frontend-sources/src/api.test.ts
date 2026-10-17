import { beforeEach, expect, it, vi } from "vitest";
import { objectFetch, setAuth } from "./api";

const auth = {
  member: { id: "member", display_name: "测试", active: true },
  csrf_token: "private-test-token",
  expires_at: "2027-01-01T00:00:00Z",
};
let transport: ReturnType<typeof vi.fn<typeof fetch>>;
beforeEach(() => {
  setAuth(auth);
  transport = vi.fn<typeof fetch>().mockResolvedValue(new Response());
  vi.stubGlobal("fetch", transport);
});
it("同源字节PUT携带会话与当前CSRF，保留PDF类型与文件且不手设长度", async () => {
  const file = new File(["%PDF"], "source.pdf", { type: "application/pdf" });
  await objectFetch("/api/v1/object-transfers/uploads/upload-1?ticket=test", {
    method: "PUT",
    body: file,
    headers: { "Content-Type": "application/pdf" },
  });
  const options = transport.mock.calls[0][1]!;
  expect(options.credentials).toBe("same-origin");
  expect(options.body).toBe(file);
  const headers = new Headers(options.headers);
  expect(headers.get("X-CSRF-Token")).toBe(auth.csrf_token);
  expect(headers.get("Content-Type")).toBe("application/pdf");
  expect(headers.has("Content-Length")).toBe(false);
});
it("外域S3不接收Cookie或CSRF，即使调用方误传CSRF也会移除", async () => {
  await objectFetch("https://private-object.example/upload?signature=test", {
    method: "PUT",
    credentials: "include",
    headers: { "X-CSRF-Token": "mistaken", "Content-Type": "application/pdf" },
  });
  const options = transport.mock.calls[0][1]!;
  expect(options.credentials).toBe("omit");
  expect(new Headers(options.headers).has("X-CSRF-Token")).toBe(false);
});
it("同源绝对原页地址携带Cookie，GET不发送CSRF并保留取消信号", async () => {
  const controller = new AbortController();
  await objectFetch(
    `${window.location.origin}/api/v1/object-transfers/versions/v1?ticket=test`,
    { signal: controller.signal },
  );
  const options = transport.mock.calls[0][1]!;
  expect(options.credentials).toBe("same-origin");
  expect(options.signal).toBe(controller.signal);
  expect(new Headers(options.headers).has("X-CSRF-Token")).toBe(false);
});
