/**
 * Reusable Button component for the UI package.
 */

export interface ButtonProps {
  label: string;
  onClick: () => void;
  variant?: "primary" | "secondary";
}

export function Button({ label, onClick, variant = "primary" }: ButtonProps) {
  return { type: "button", props: { label, onClick, variant } };
}
