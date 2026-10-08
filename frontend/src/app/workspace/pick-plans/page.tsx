import { PlansWorkspace } from "@/components/workspace/pick/plans/plans-workspace";
import {
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";
export const metadata = { title: "排期草稿" };
export default async function PlansPage({
  searchParams,
}: {
  searchParams: Promise<{ selection?: string | string[] }>;
}) {
  const query = await searchParams;
  const ids =
    typeof query.selection === "string"
      ? query.selection.split(",").slice(0, 100)
      : [];
  return (
    <WorkspaceContainer>
      <WorkspaceHeader />
      <div className="relative flex min-h-0 w-full flex-1 flex-col items-center overflow-y-auto">
        <PlansWorkspace selectionIds={ids} />
      </div>
    </WorkspaceContainer>
  );
}
