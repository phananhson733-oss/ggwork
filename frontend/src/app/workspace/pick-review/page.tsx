import { PostReview } from "@/components/workspace/pick/post-review";
import {
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";
export const metadata = { title: "发布复盘" };
export default function ReviewPage() {
  return (
    <WorkspaceContainer>
      <WorkspaceHeader />
      <div className="relative flex min-h-0 w-full flex-1 flex-col items-center overflow-y-auto">
        <PostReview />
      </div>
    </WorkspaceContainer>
  );
}
