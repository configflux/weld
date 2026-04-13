/**
 * Root layout wrapper for the web application.
 */

import { Card } from "@acme/ui";

export interface LayoutProps {
  children: unknown;
}

export function Layout({ children }: LayoutProps) {
  return Card({ title: "Acme App", children });
}
