import { EditingForm } from "@/components/workspace/editing/editing-form";
import { EditingShell } from "@/components/workspace/editing/editing-shell";
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ title?: string; returnTo?: string }>;
}) {
  const params = await searchParams;
  return (
    <EditingShell title="新建剪辑">
      <EditingForm initialTitle={params.title} returnTo={params.returnTo} />
    </EditingShell>
  );
}
