import { MySelections } from "@/components/workspace/pick/my-selections";
import {
  WorkspaceBody,
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";

export default function PicksPage() {
  return (
    <WorkspaceContainer>
      <WorkspaceHeader />
      <WorkspaceBody>
        <MySelections />
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}
