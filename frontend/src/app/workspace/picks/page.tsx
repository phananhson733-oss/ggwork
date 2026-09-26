import type { Metadata } from "next";

import { MySelections } from "@/components/workspace/pick/my-selections";
import {
  WorkspaceBody,
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";

export const metadata: Metadata = { title: "我的选剧" };

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
