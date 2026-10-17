import type { NextAction } from "./contracts";
export const workspacePages = [
  ["overview", "项目概览"],
  ["materials", "项目资料"],
  ["review", "评审与风险"],
  ["matrix", "响应矩阵"],
  ["rules", "规则核对"],
  ["facts", "工程事实"],
  ["operations", "处理记录"],
  ["settings", "项目设置"],
] as const;
export type WorkspacePage = (typeof workspacePages)[number][0];
export function parseWorkspacePage(pathname: string): WorkspacePage | "home" {
  const value = pathname.split("/")[3];
  return workspacePages.some(([page]) => page === value)
    ? (value as WorkspacePage)
    : "home";
}

export const verdictNames = {
  satisfied: "满足",
  violated: "违反",
  not_found: "未找到响应",
  unable_to_judge: "无法判断",
  not_applicable: "不适用",
};
export const confirmationNames = {
  pending: "待核对",
  confirmed: "已确认",
  rejected: "已驳回",
  deferred: "暂缓",
};
export const ruleKindNames = {
  disqualification: "否决要求",
  mandatory: "强制要求",
  scoring: "评分条款",
  advisory: "一般建议",
};

export function actionTarget(bidId: string, action: NextAction) {
  const target = action.target_url.match(
    new RegExp(`^/bids/${bidId}/([^/?#]+)`),
  )?.[1];
  const semantic: Record<string, WorkspacePage> = {
    upload_materials: "materials",
    start_review: "review",
    review: "review",
    confirm_rules: "rules",
    confirm_facts: "facts",
  };
  const page =
    workspacePages.find(([key]) => key === target)?.[0] ??
    semantic[action.action];
  const reviewId = action.target_url.startsWith(`/bids/${bidId}/`)
    ? (new URLSearchParams(action.target_url.split("?")[1] ?? "").get(
        "review",
      ) ?? undefined)
    : undefined;
  return page ? { page, reviewId } : null;
}
