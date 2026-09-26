import { redirect } from "next/navigation";

// GGWork has no public landing page. /workspace sends signed-out visitors to
// /login and signed-in ones (or the static demo build) into the app.
export default function RootPage() {
  return redirect("/workspace");
}
