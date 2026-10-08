const labels: Record<string, string> = {
  profile_unavailable: "此剪辑模式尚未就绪，请联系管理员检查能力配置",
  source_changed: "素材已变化，请重新检查素材后继续",
  directory_missing: "Mac 未找到素材目录，请检查目录位置与授权",
  symlink_denied: "素材链接超出授权范围，请选择授权目录内的原文件",
  episode_order_ambiguous: "无法确定集数顺序，请明确每个文件的集号后重新提交",
  source_directory_empty: "素材目录没有可用视频，请检查目录后重新选择",
  source_identity_ambiguous: "素材身份重复或不明确，请检查文件与集号",
  source_manifest_empty: "素材清单为空，请重新选择本次素材",
  source_audio_video_required:
    "素材需要同时包含视频和音轨，请在 Mac 检查所选文件",
  model_language_unsupported: "本地转录模型不支持该语言，请在 Mac 配置对应模型",
  language_unsupported: "当前不支持该素材语言，请检查语言设置",
  native_process_failed: "Mac 本地处理未完成，请检查执行器环境后重试",
  preparation_failed: "素材准备未完成，请在 Mac 检查目录权限与文件后重新检查",
  render_failed: "视频渲染失败，请检查 Mac 环境与可用磁盘空间后重试此条",
  decode_failed: "成片解码检查未通过，请检查 Mac 环境后重试此条",
  planning_failed: "剪辑方案生成失败，请检查规划服务后重试该阶段",
  transcribing_failed: "素材转录失败，请检查本地转录模型与素材后重试该阶段",
  unsupported_platform:
    "当前执行器支持 Apple Silicon Mac，请在支持的设备上连接",
  disk_full: "Mac 可用磁盘空间不足，请释放空间后重试",
  ffmpeg_missing: "Mac 缺少 ffmpeg，请安装后重新检查环境",
  ffprobe_missing: "Mac 缺少 ffprobe，请安装后重新检查环境",
  whisper_missing: "Mac 缺少 whisper-cli，请安装后重新检查环境",
  model_missing: "Mac 未找到转录模型，请配置本地模型后重新检查环境",
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
  preparing: "准备素材",
  finished: "处理已结束",
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
export const editingLabel = (value: string) =>
  labels[value] ?? "状态暂不可识别，请重新检查；如仍未恢复，请联系管理员";
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
