import { DataImports } from "@/components/workspace/pick/data-imports";
import {
  WorkspaceBody,
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";

export default function PickDataPage() {
  return (
    <WorkspaceContainer>
      <WorkspaceHeader />
      <WorkspaceBody>
        <DataImports />
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}
