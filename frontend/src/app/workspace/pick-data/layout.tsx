import "./pick-board.css";

/**
 * Style-only boundary: the pick-board palette loads with this route and no
 * other. Authentication stays with the workspace layout and the page.
 */
export default function PickDataLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return children;
}
