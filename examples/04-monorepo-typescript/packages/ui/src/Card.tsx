/**
 * Card layout component.
 */

export interface CardProps {
  title: string;
  children?: unknown;
}

export function Card({ title, children }: CardProps) {
  return { type: "div", props: { className: "card", title, children } };
}
