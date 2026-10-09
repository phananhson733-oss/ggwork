import { EditingShell } from "@/components/workspace/editing/editing-shell";
import { EditingTaskView } from "@/components/workspace/editing/editing-task";
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ task_id: string }>;
  searchParams: Promise<{ returnTo?: string }>;
}) {
  const { task_id } = await params;
  const { returnTo } = await searchParams;
  return (
    <EditingShell title="剪辑详情">
      <EditingTaskView key={task_id} taskId={task_id} returnTo={returnTo} />
    </EditingShell>
  );
}
