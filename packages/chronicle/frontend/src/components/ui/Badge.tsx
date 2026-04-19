import { badgeBase, badgeVariants } from "@/theme/styles";

interface BadgeProps {
  variant: string;
  children: React.ReactNode;
}

/** Solid-fill badge */
export function Badge({ variant, children }: BadgeProps) {
  const variantClass = badgeVariants[variant] ?? badgeVariants.default;
  return (
    <span className={`${badgeBase} ${variantClass}`}>
      {children}
    </span>
  );
}
