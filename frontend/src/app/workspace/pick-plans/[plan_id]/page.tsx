import { PlansWorkspace } from "@/components/workspace/pick/plans/plans-workspace";
import {
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";
export const metadata = { title: "编辑排期" };
export default async function PlanPage({
  params,
}: {
  params: Promise<{ plan_id: string }>;
}) {
  const { plan_id } = await params;
  return (
    <WorkspaceContainer>
      <WorkspaceHeader />
      <div className="relative flex min-h-0 w-full flex-1 flex-col items-center overflow-y-auto">
        <PlansWorkspace planId={plan_id} />
      </div>
    </WorkspaceContainer>
  );
}
