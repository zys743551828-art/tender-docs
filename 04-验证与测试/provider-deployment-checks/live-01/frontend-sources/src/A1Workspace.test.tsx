import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { CoverageSummary } from "./CoverageSummary";
import { DecisionForm } from "./DecisionForm";
import { FactsPanel } from "./FactsPanel";
import { MatrixPanel } from "./MatrixPanel";
import { ProjectWorkspace } from "./ProjectWorkspace";
import { ResourceState } from "./ResourceState";
import { ReviewPanel } from "./ReviewPanel";
import { RulesPanel } from "./RulesPanel";
import { useResource } from "./useResource";
import type {
  Bid,
  FindingView,
  ReviewCoverage,
  ReviewView,
  RuleView,
} from "./contracts";

// These routes and responses are explicit component-test fixtures, not deployed APIs.
vi.mock("./contracts", async (original) => {
  const actual = await original<typeof import("./contracts")>();
  return {
    ...actual,
    implementedOperations: [
      ...actual.implementedOperations,
      "GET /api/v1/reviews/{review_id}",
      "GET /api/v1/reviews/{review_id}/findings",
      "GET /api/v1/reviews/{review_id}/matrix",
      "GET /api/v1/bids/{bid_id}/facts",
      "POST /api/v1/bids/{bid_id}/facts",
      "GET /api/v1/bids/{bid_id}/rules",
      "GET /api/v1/rules/{rule_id}",
      "POST /api/v1/rules/{rule_id}/decisions",
      "POST /api/v1/rule-sets/{rule_set_id}/confirmations",
    ],
  };
});
const bid: Bid = {
  id: "bid-1",
  title: "测试项目",
  revision: 1,
  material_revision: 1,
  presentation_revision: 1,
  phase: "working",
  archived_at: null,
  current_body_id: null,
  current_review_id: "review-current",
  stream_id: "stream-1",
  next_action: {
    action: "start_review",
    reason: "查看当前核心缺口",
    target_url: "/bids/bid-1/review",
    blocked: false,
  },
};
const layer = {
  state: "complete" as const,
  planned: 10,
  completed: 10,
  denominator_basis: "本轮明确识别的10项",
  gaps: [],
};
const coverage: ReviewCoverage = {
  source_scope: {
    ...layer,
    state: "partial",
    completed: 8,
    gaps: ["扫描页尚未核对"],
  },
  core_extraction: layer,
  judgment: layer,
  scoring: { ...layer, state: "not_run", completed: 0 },
  extraction_quality: "not_measured",
  quality_report_id: null,
  human_confirmation_pending: 4,
};
const review: ReviewView = {
  id: "review-current",
  bid_id: bid.id,
  operation_id: "op-review",
  input_digest: "a".repeat(64),
  core_coverage: "partial",
  risk_count: 2,
  current: true,
  prediction: {
    review_id: "review-current",
    mode: "hidden",
    maximum: null,
    reasons: [],
    method_digest: "b".repeat(64),
  },
  next_action: bid.next_action,
  source_impact_revision: 1,
  manifest_digest: "c".repeat(64),
  coverage_layers: coverage,
};
function rule(
  id: string,
  confirmation: RuleView["confirmation"] = "pending",
): RuleView {
  return {
    id,
    rule_set_id: "rules-1",
    kind: "mandatory",
    statement: `规则 ${id}`,
    conditions: [],
    exceptions: [],
    maximum: null,
    evidence_ids: [],
    confirmation,
    decision_revision: 1,
    carryover: null,
    scope: { bid_id: bid.id, description: "当前标段" },
    bands: [],
    origin_kind: "model_extracted",
    manual_origin_rule_id: null,
    provenance_status: "effective",
  };
}
function finding(id: string): FindingView {
  return {
    id,
    review_id: review.id,
    rule_id: null,
    category: "consistency",
    severity: "critical",
    verdict: "violated",
    statement: `独立关键风险 ${id}`,
    evidence_ids: [],
    searched_ranges: ["完整正文"],
    decision: null,
    decision_revision: 1,
    origin_review_id: "review-origin",
    retained_known_critical: true,
    detection_run_state: "not_run_this_round",
    lifecycle: "open",
  };
}
function json(data: unknown, status = 200, etag?: string) {
  return new Response(JSON.stringify(data), {
    status,
    headers: etag ? { ETag: etag } : {},
  });
}
let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>;
beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  fetchMock = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fetchMock);
  HTMLElement.prototype.scrollIntoView = vi.fn();
});

it("来源、提取、判断、评分与人工确认分层，10/10不会写成原文零遗漏", () => {
  render(<CoverageSummary coverage={coverage} />);
  expect(screen.getByText("扫描页尚未核对")).toBeVisible();
  expect(screen.getByText("检测质量待验证")).toBeVisible();
  expect(screen.getByText("待人工确认：4 项")).toBeVisible();
  expect(screen.getByText("评分条款处理")).toBeVisible();
  expect(screen.queryByText(/100%|零遗漏|全稿通过/)).not.toBeInTheDocument();
});

it("保留两项旧关键风险，不因本轮未检测或隐藏分数而消失", async () => {
  fetchMock.mockImplementation(async (url) => {
    if (url === "/api/v1/reviews/review-current") return json(review);
    if (url === "/api/v1/reviews/review-current/findings")
      return json({
        items: [finding("一"), finding("二")],
        next_cursor: null,
        has_more: false,
      });
    if (url === "/api/v1/bids/bid-1") return json(bid, 200, '"bid:bid-1:1"');
    if (url === "/api/v1/bids/bid-1/materials")
      return json({ documents: [], conflicts: [] });
    return json({
      id: "op-review",
      bid_id: bid.id,
      kind: "review",
      state: "succeeded",
    });
  });
  render(
    <ReviewPanel
      bid={bid}
      reviewId={review.id}
      onNavigate={vi.fn()}
      onChanged={vi.fn()}
    />,
  );
  expect(await screen.findByText("独立关键风险 一")).toBeVisible();
  expect(screen.getByText("独立关键风险 二")).toBeVisible();
  expect(
    screen.getAllByText("先前检测发现，本轮未运行该检测；该风险仍保留待处理。"),
  ).toHaveLength(2);
  expect(screen.queryByText("已解决")).not.toBeInTheDocument();
  expect(
    screen.getByText("尚未解决的关键风险 · 当前已加载 2 项"),
  ).toBeVisible();
});

it("矩阵将检查失败与成功后的无法判断分开，不给失败项假满足", async () => {
  fetchMock.mockResolvedValue(
    json({
      review_id: review.id,
      coverage: "partial",
      coverage_layers: coverage,
      rows: [
        {
          rule: rule("失败"),
          verdict: null,
          finding_ids: [],
          evidence_ids: [],
          check_status: "failed",
          error_code: "MODEL_OUTPUT_INVALID",
        },
        {
          rule: rule("证据不足"),
          verdict: "unable_to_judge",
          finding_ids: [],
          evidence_ids: [],
          check_status: "succeeded",
          error_code: null,
        },
      ],
    }),
  );
  render(<MatrixPanel reviewId={review.id} bidId={bid.id} />);
  const failed = (await screen.findByText("规则 失败")).closest("article")!;
  const unknown = screen.getByText("规则 证据不足").closest("article")!;
  expect(within(failed).getByText("检查失败")).toBeVisible();
  expect(within(failed).getByText("尚未形成有效判断")).toBeVisible();
  expect(within(unknown).getByText("执行已完成")).toBeVisible();
  expect(within(unknown).getByText("无法判断")).toBeVisible();
  expect(
    within(failed).queryByText("满足", { exact: true }),
  ).not.toBeInTheDocument();
});

it("事实未知和布尔否不替换成0，空事实库不加首次评审前置", async () => {
  const base = {
    subject: "标段一",
    scope: "本项目",
    unit: null,
    evidence_ids: [],
    confirmation: "pending",
    decision_revision: 1,
  };
  fetchMock.mockResolvedValue(
    json({
      id: "facts-1",
      bid_id: bid.id,
      revision: 1,
      facts: [
        { ...base, id: "fact-unknown", name: "地下水位", value: null },
        { ...base, id: "fact-false", name: "已获批夜间施工", value: false },
      ],
    }),
  );
  render(<FactsPanel bid={bid} onChanged={vi.fn()} />);
  expect(await screen.findByText("未知／尚未明确")).toBeVisible();
  expect(screen.getByText("否", { exact: true })).toBeVisible();
  expect(screen.queryByText("0", { exact: true })).not.toBeInTheDocument();
});

it("批量规则只提交明确勾选的pending项，不覆盖驳回和暂缓", async () => {
  const user = userEvent.setup();
  fetchMock.mockImplementation(async (url, options) => {
    if (url === "/api/v1/rule-sets/rules-1/confirmations") {
      expect(options?.headers).toMatchObject({
        "If-Match": '"ruleset:rules-1:1"',
      });
      expect(JSON.parse(options?.body as string)).toEqual({
        items: [{ rule_id: "pending", expected_decision_revision: 1 }],
      });
      return json({
        rule_set_id: "rules-1",
        revision: 2,
        items: [
          {
            rule_id: "pending",
            status: "confirmed",
            decision_revision: 2,
            decision_id: "decision-1",
          },
        ],
      });
    }
    return json(
      {
        id: "rules-1",
        bid_id: bid.id,
        material_revision: 1,
        revision: 1,
        coverage: "partial",
        rules: [
          rule("pending"),
          rule("rejected", "rejected"),
          rule("deferred", "deferred"),
        ],
      },
      200,
      '"ruleset:rules-1:1"',
    );
  });
  render(<RulesPanel bid={bid} onChanged={vi.fn()} />);
  const selected = await screen.findByRole("checkbox", {
    name: "选择条款：规则 pending",
  });
  expect(
    screen.getByRole("checkbox", { name: "选择条款：规则 rejected" }),
  ).toBeDisabled();
  expect(
    screen.getByRole("checkbox", { name: "选择条款：规则 deferred" }),
  ).toBeDisabled();
  await user.click(selected);
  await user.click(
    screen.getByRole("button", { name: "确认所选 1 项条款解释" }),
  );
  expect(await screen.findByRole("status")).toHaveTextContent(
    "条款解释确认不代表正文满足",
  );
});

it("单条决定对象已变化时保留输入并禁止旧解释配新ETag提交", async () => {
  const user = userEvent.setup();
  fetchMock.mockResolvedValue(
    json(
      { ...rule("rule-1"), decision_revision: 2 },
      200,
      '"decision:rule-1:2"',
    ),
  );
  render(
    <DecisionForm
      kind="rules"
      id="rule-1"
      revision={1}
      evidenceIds={[]}
      onSaved={vi.fn()}
    />,
  );
  await user.click(screen.getByRole("button", { name: "处理本条" }));
  await user.type(
    screen.getByLabelText("依据与原因"),
    "已对照当前原文，但对象有并发变化",
  );
  expect(
    await screen.findByText(
      "本条记录已变化，输入已保留。请刷新并重新核对对象，再明确提交。",
    ),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "确认条款解释" })).toBeDisabled();
  expect(
    fetchMock.mock.calls.some(([, options]) => options?.method === "POST"),
  ).toBe(false);
});

it("显式历史轮次优先，不拿current指针替换所指review", async () => {
  fetchMock.mockImplementation(async (url) => {
    if (url === "/api/v1/bids/bid-1") return json(bid);
    if (url === "/api/v1/reviews/review-old")
      return json({ ...review, id: "review-old", current: false });
    if (String(url).endsWith("/findings"))
      return json({ items: [], next_cursor: null, has_more: false });
    if (String(url).endsWith("/materials"))
      return json({ documents: [], conflicts: [] });
    return json({
      id: "op-review",
      bid_id: bid.id,
      state: "succeeded",
      kind: "review",
    });
  });
  render(
    <ProjectWorkspace
      id={bid.id}
      route="/bids/bid-1/review?review=review-old"
      onBack={vi.fn()}
      onNavigate={vi.fn()}
    />,
  );
  expect(await screen.findByText("历史评审 · 输入已固定")).toBeVisible();
  expect(
    fetchMock.mock.calls.some(([url]) => url === "/api/v1/reviews/review-old"),
  ).toBe(true);
  expect(
    fetchMock.mock.calls.some(
      ([url]) => url === "/api/v1/reviews/review-current",
    ),
  ).toBe(false);
});

it("读取刷新失败保留已知记录并显式标明未更新，不伪造空成功", async () => {
  const user = userEvent.setup();
  let attempts = 0;
  fetchMock.mockImplementation(async () => {
    if (++attempts === 1) return json(bid);
    throw new TypeError("unavailable");
  });
  function Subject() {
    const resource = useResource<Bid>("/bids/bid-1");
    return (
      <>
        <ResourceState
          {...resource}
          hasData={!!resource.data}
          onRetry={() => void resource.reload()}
        />
        <h2>{resource.data?.title}</h2>
        <button onClick={() => void resource.reload()}>刷新测试记录</button>
      </>
    );
  }
  render(<Subject />);
  expect(
    await screen.findByRole("heading", { name: "测试项目" }),
  ).toBeVisible();
  await user.click(screen.getByRole("button", { name: "刷新测试记录" }));
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent("暂时无法连接服务"),
  );
  expect(screen.getByRole("heading", { name: "测试项目" })).toBeVisible();
  expect(
    screen.getByText(
      "暂时无法更新，下面保留上次读取的记录；请刷新核对后再作决定。",
    ),
  ).toBeVisible();
});
