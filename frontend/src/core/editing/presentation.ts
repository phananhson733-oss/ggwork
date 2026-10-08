const labels: Record<string, string> = {
  device_required: "请连接并选择 Mac",
  sources_required: "请提供素材",
  sources_unverified: "等待全部素材本地校验",
  directory_not_authorized: "请在 Mac 授权该素材目录",
  available: "本地文件已通过本次访问检查",
  transfer_timeout_restart_required: "传输超时，请重新提交未完成文件",
  relay_capacity: "设备传输繁忙，请稍后重新检查",
  waiting: "待准备",
  queued: "等待 Mac 开始处理",
  running: "处理中",
  awaiting_plan: "等待你确认方案",
  stopping: "已请求停止，等待设备确认",
  completed: "已完成",
  partial: "部分完成",
  failed: "失败",
  stopped: "已停止",
  transcribing: "转录素材",
  planning: "生成方案",
  rendering: "渲染视频",
  verifying: "检查成片",
  selected: "待提交",
  received: "已收到，正在校验",
  verified: "已校验",
  pending: "待处理",
  device_offline: "生成设备离线，请在 Mac 启动执行器",
  device_revoked: "设备授权已撤销，请重新连接设备",
  unchecked: "取片时将验证本地文件",
  file_missing: "生成设备未找到原成片，请在 Mac 检查文件",
  file_changed: "文件已变化，不能作为原成片交付",
  grant_denied: "目录访问未获授权，请在 Mac 检查授权",
  transfer_failed: "视频传输中断，请重试访问",
  planner_unavailable: "云端规划尚未配置",
  skill_disabled: "剪辑功能已停用",
  device_not_ready: "剪辑环境尚未就绪",
  missing_device: "请连接并选择 Mac",
  missing_sources: "请提供素材",
  source_not_verified: "等待所有素材校验",
  highlight: "原声高光",
  hook: "Hook 剪辑",
};
export const editingLabel = (value: string) => labels[value] ?? value;
export function safeEditingReturn(value: string | null) {
  if (!value || !value.startsWith("/workspace/") || value.includes("\\"))
    return null;
  try {
    const url = new URL(value, "https://local.invalid");
    return url.origin === "https://local.invalid" &&
      ["/workspace/pick-data", "/workspace/picks"].includes(url.pathname)
      ? `${url.pathname}${url.search}${url.hash}`
      : null;
  } catch {
    return null;
  }
}
export function editingCreateHref(title: string, returnTo: string) {
  return `/workspace/editing/new?${new URLSearchParams({ title, returnTo })}`;
}
export function editingTaskId(raw: unknown): string | null {
  try {
    const value = (typeof raw === "string" ? JSON.parse(raw) : raw) as {
      task?: { id?: unknown };
      task_id?: unknown;
      id?: unknown;
    } | null;
    const id = value?.task?.id ?? value?.task_id ?? value?.id;
    return typeof id === "string" && id.length > 0 && id.length < 200
      ? id
      : null;
  } catch {
    return null;
  }
}
