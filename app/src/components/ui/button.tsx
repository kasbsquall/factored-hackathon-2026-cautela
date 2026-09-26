"use client";

import Link from "next/link";
import { CircleNotch } from "@phosphor-icons/react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import styles from "./button.module.css";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "md" | "sm";

interface CommonProps {
  variant?: Variant;
  size?: Size;
  icon?: ReactNode;
  /** Primary buttons nest this icon in its own circle, flush right. */
  trailing?: ReactNode;
}

interface ButtonProps extends CommonProps, ButtonHTMLAttributes<HTMLButtonElement> {
  loading?: boolean;
  loadingLabel?: string;
}

function classes(variant: Variant, size: Size, extra?: string): string {
  return [styles.btn, styles[variant], styles[size], extra].filter(Boolean).join(" ");
}

function Content({ variant, icon, trailing, children }: CommonProps & { children: ReactNode }) {
  return (
    <>
      {icon}
      <span className={styles.label}>{children}</span>
      {trailing && variant === "primary" ? <span className={styles.nest}>{trailing}</span> : trailing}
    </>
  );
}

export function Button({
  variant = "primary", size = "md", icon, trailing, loading = false, loadingLabel, className, children, disabled, onClick,
  "aria-disabled": ariaDisabled, ...rest
}: ButtonProps) {
  // aria-disabled keeps the button focusable and announced; it must also stop the click.
  const inert = loading || ariaDisabled === true || ariaDisabled === "true";
  return (
    <button
      type="button"
      {...rest}
      className={classes(variant, size, `${loading ? styles.loading : ""} ${className ?? ""}`)}
      disabled={disabled}
      aria-busy={loading || undefined}
      aria-disabled={inert || undefined}
      onClick={inert ? (event) => event.preventDefault() : onClick}
    >
      {loading ? (
        <>
          <CircleNotch className={styles.spinner} aria-hidden />
          <span className={styles.label}>{loadingLabel ?? children}</span>
        </>
      ) : (
        <Content variant={variant} icon={icon} trailing={trailing}>{children}</Content>
      )}
    </button>
  );
}

interface ButtonLinkProps extends CommonProps {
  href: string;
  children: ReactNode;
  className?: string;
}

export function ButtonLink({ href, variant = "secondary", size = "md", icon, trailing, className, children }: ButtonLinkProps) {
  return (
    <Link href={href} className={classes(variant, size, className)}>
      <Content variant={variant} icon={icon} trailing={trailing}>{children}</Content>
    </Link>
  );
}
