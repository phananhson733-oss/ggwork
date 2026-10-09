import type { RadarPoint } from "@/core/radar/schema";

export function RadarCurve({
  points,
  large = false,
}: {
  points: RadarPoint[] | null;
  large?: boolean;
}) {
  if (!points?.length)
    return <p className="text-muted-foreground">没有可用曲线</p>;
  const x = (i: number) =>
    points.length === 1 ? 150 : 5 + (i / (points.length - 1)) * 290;
  return (
    <svg
      role="img"
      aria-label={`${points.length} 个历史日点，相对兴趣指数：${points.map((p) => p.value).join("、")}`}
      viewBox="0 0 300 110"
      className={large ? "text-link h-52 w-full" : "text-link h-14 w-48"}
    >
      <path d="M5 5V105H295" stroke="currentColor" opacity="0.2" fill="none" />
      <polyline
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        points={points.map((p, i) => `${x(i)},${105 - p.value}`).join(" ")}
      />
      {points.map((p, i) => (
        <circle
          key={p.date}
          cx={x(i)}
          cy={105 - p.value}
          r={large ? 2.5 : 1.5}
          fill="currentColor"
        >
          <title>
            {p.date}：{p.value}
          </title>
        </circle>
      ))}
    </svg>
  );
}
