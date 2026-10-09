const ITEMS = [
  "资料由 GGWork 采集并发布版本；源数据日期与工作台版本日期分别展示。",
  "网盘与官方取货链接按资料所有者权限读取，不进入共享镜像和智能体证据。",
  "订单按日期、剧与推广类型汇总；工作台不显示原始分成金额。",
  "原公开网站已停止服务。历史点击和搜索表现保留原日期，不作为新采集的流量。",
] as const;
export function DifferencesNote() {
  return (
    <section className="border-line bg-panel mt-6 rounded-lg border px-4 py-3 text-[13px]">
      <h2 className="text-ink-2 font-semibold">数据与资源说明</h2>
      <ul className="text-helper mt-2 list-disc space-y-1 pl-5 leading-[1.65]">
        {ITEMS.map((text) => (
          <li key={text}>{text}</li>
        ))}
      </ul>
    </section>
  );
}
