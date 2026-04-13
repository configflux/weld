/**
 * Root application component.
 *
 * Imports UI components from @acme/ui and data-fetching
 * utilities from @acme/api -- demonstrating cross-package
 * dependency edges in the knowledge graph.
 */

import { Button, Card } from "@acme/ui";
import { fetchItems } from "@acme/api";

export function App() {
  return {
    type: "main",
    children: [
      Card({ title: "Items", children: Button({ label: "Refresh", onClick: () => fetchItems("/api") }) }),
    ],
  };
}
