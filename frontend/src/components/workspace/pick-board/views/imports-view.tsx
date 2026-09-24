// 工作台新建：「同步与导入」tab。原来的资料页（同步状态加手动导入）原样放进资料页的一个 tab：
// 它只走浏览器端的 /api/pick 接口，不读镜像库，所以没有镜像、或镜像读不了时也能用。
import { DataImports } from "@/components/workspace/pick/data-imports";

export function ImportsView() {
  return (
    <div className="max-w-4xl">
      <DataImports />
    </div>
  );
}
