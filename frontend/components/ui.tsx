/** Small local UI kit on top of Tailwind utilities (no component library). */
import type { ButtonHTMLAttributes, ComponentPropsWithoutRef, ElementType, ReactNode } from "react";
import { twMerge } from "tailwind-merge";

/** Join classes; later utilities win over conflicting defaults (e.g. Card `p-0`, Badge `text-lg`). */
export const cx = (...c: (string | false | null | undefined)[]) => twMerge(c.filter(Boolean).join(" "));

// ---------------------------------------------------------------- buttons
type Variant = "primary" | "secondary" | "ghost" | "danger" | "success";
type Size = "sm" | "md" | "lg";
const VARIANT: Record<Variant, string> = {
  primary: "bg-brand-700 text-white shadow-sm hover:bg-brand-800 dark:bg-brand-400 dark:text-brand-950 dark:hover:bg-brand-300",
  secondary: "border border-line bg-card text-brand-800 hover:border-brand-300 hover:bg-brand-50 dark:text-brand-200 dark:hover:border-brand-700 dark:hover:bg-brand-900/40",
  ghost: "text-muted hover:bg-soft hover:text-ink",
  danger: "border border-danger-200 bg-card text-danger-700 hover:bg-danger-50 dark:border-danger-900 dark:text-danger-300 dark:hover:bg-danger-950/60",
  success: "bg-success-700 text-white shadow-sm hover:bg-success-800 dark:bg-success-500 dark:text-success-950 dark:hover:bg-success-300",
};
const SIZE: Record<Size, string> = {
  sm: "min-h-10 px-3 text-sm",
  md: "min-h-11 px-4 text-[15px]",
  lg: "min-h-13 px-6 text-lg",
};
export function buttonClass(variant: Variant = "primary", size: Size = "md", className?: string) {
  return cx(
    "inline-flex select-none items-center justify-center gap-2 rounded-xl font-semibold transition-colors",
    "disabled:cursor-not-allowed disabled:opacity-50",
    VARIANT[variant], SIZE[size], className,
  );
}
export function Button({ variant, size, className, type = "button", ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size }) {
  return <button type={type} className={buttonClass(variant, size, className)} {...rest} />;
}

// ---------------------------------------------------------------- surfaces
type CardProps<T extends ElementType> = { as?: T; className?: string } & Omit<ComponentPropsWithoutRef<T>, "as" | "className">;
export function Card<T extends ElementType = "section">({ as, className, ...rest }: CardProps<T>) {
  const Tag = (as ?? "section") as ElementType;
  return <Tag className={cx("rounded-(--radius-card) border border-line bg-card p-5 shadow-(--shadow-card) sm:p-6", className)} {...rest} />;
}
export function CardTitle({ children, className, as: Tag = "h2", aside }: { children: ReactNode; className?: string; as?: "h2" | "h3"; aside?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
      <Tag className={cx("text-lg font-bold tracking-tight text-ink", className)}>{children}</Tag>
      {aside}
    </div>
  );
}
export const Muted = ({ children, className }: { children: ReactNode; className?: string }) =>
  <p className={cx("text-sm leading-relaxed text-muted", className)}>{children}</p>;

// ---------------------------------------------------------------- form controls
export const inputClass = cx(
  "block w-full min-h-11 rounded-xl border border-line bg-card px-3.5 py-2 text-base text-ink shadow-xs",
  "placeholder:text-muted/70 focus:border-brand-500 disabled:opacity-60",
);
/** Label + control (+ hint) stacked; the control is passed as children so every input keeps its props. */
export function Field({ label, hint, children, className }: { label: ReactNode; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <label className={cx("block", className)}>
      <span className="mb-1.5 block text-sm font-semibold text-ink">
        {label}{hint && <span className="ml-1.5 font-normal text-muted">{hint}</span>}
      </span>
      {children}
    </label>
  );
}
export const checkClass = "mt-0.5 size-5 shrink-0 accent-brand-700 dark:accent-brand-400";
export function Check({ children, className, ...input }: ComponentPropsWithoutRef<"input"> & { children: ReactNode }) {
  return (
    <label className={cx("flex min-h-11 cursor-pointer items-start gap-3 py-2 text-[15px] leading-snug", className)}>
      <input type="checkbox" className={checkClass} {...input} />
      <span>{children}</span>
    </label>
  );
}

// ---------------------------------------------------------------- feedback
type Tone = "brand" | "success" | "warning" | "danger" | "info" | "neutral";
const BADGE: Record<Tone, string> = {
  brand: "bg-brand-100 text-brand-800 dark:bg-brand-900/60 dark:text-brand-200",
  success: "bg-success-100 text-success-800 dark:bg-success-900/60 dark:text-success-200",
  warning: "bg-warning-100 text-warning-900 dark:bg-warning-900/50 dark:text-warning-200",
  danger: "bg-danger-100 text-danger-800 dark:bg-danger-900/60 dark:text-danger-200",
  info: "bg-info-100 text-info-800 dark:bg-info-900/60 dark:text-info-200",
  neutral: "bg-soft text-muted",
};
export function Badge({ tone = "neutral", className, ...rest }: ComponentPropsWithoutRef<"span"> & { tone?: Tone }) {
  return <span className={cx("inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold", BADGE[tone], className)} {...rest} />;
}
const ALERT: Record<Tone, string> = {
  brand: "border-brand-200 bg-brand-50 text-brand-900 dark:border-brand-800 dark:bg-brand-950/60 dark:text-brand-100",
  success: "border-success-200 bg-success-50 text-success-900 dark:border-success-800 dark:bg-success-950/60 dark:text-success-100",
  warning: "border-warning-200 bg-warning-50 text-warning-900 dark:border-warning-800 dark:bg-warning-950/60 dark:text-warning-100",
  danger: "border-danger-200 bg-danger-50 text-danger-800 dark:border-danger-800 dark:bg-danger-950/60 dark:text-danger-100",
  info: "border-info-200 bg-info-50 text-info-900 dark:border-info-800 dark:bg-info-950/60 dark:text-info-100",
  neutral: "border-line bg-soft text-ink",
};
/** Polymorphic so existing `<p role="alert" data-testid=…>` keep their element and attributes. */
export function Alert<T extends ElementType = "p">({ as, tone = "info", className, ...rest }: CardProps<T> & { tone?: Tone }) {
  const Tag = (as ?? "p") as ElementType;
  return <Tag className={cx("rounded-xl border px-4 py-3 text-sm leading-relaxed", ALERT[tone], className)} {...rest} />;
}

const BAR: Record<Tone, string> = {
  brand: "bg-brand-600 dark:bg-brand-400", success: "bg-success-600 dark:bg-success-500", warning: "bg-warning-500",
  danger: "bg-danger-600 dark:bg-danger-500", info: "bg-info-600 dark:bg-info-500", neutral: "bg-muted",
};
/** value in 0..1 (null → empty bar). */
export function ProgressBar({ value, tone = "brand", label, className }: { value: number | null | undefined; tone?: Tone; label: string; className?: string }) {
  const v = Math.max(0, Math.min(1, value ?? 0));
  return (
    <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(v * 100)}
      className={cx("h-2.5 w-full overflow-hidden rounded-full bg-soft", className)}>
      <div className={cx("h-full rounded-full transition-[width] duration-500", BAR[tone])} style={{ width: `${v * 100}%` }} />
    </div>
  );
}

// ---------------------------------------------------------------- brand
export function Logo({ size = "md" }: { size?: "sm" | "md" | "lg" }) {
  const s = { sm: "size-9 text-xl rounded-xl", md: "size-12 text-3xl rounded-2xl", lg: "size-20 text-5xl rounded-3xl" }[size];
  return (
    <span aria-hidden="true"
      className={cx("inline-flex shrink-0 items-center justify-center bg-linear-to-br from-brand-500 to-brand-800 font-hanja font-bold text-white shadow-md", s)}>
      漢
    </span>
  );
}

/** Centred shell for login / sign-up / billing redirects. */
export function AuthShell({ children, subtitle = "초등학생 한자 급수 학습 · 문해력 챗봇" }: { children: ReactNode; subtitle?: string }) {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center bg-linear-to-b from-brand-50 to-bg px-4 py-10 dark:from-brand-950/60">
      <div className="w-full max-w-md">
        <div className="mb-6 flex flex-col items-center text-center">
          <Logo size="lg" />
          <h1 className="mt-4 text-3xl font-extrabold tracking-tight text-ink">한자 튜터</h1>
          <p className="mt-1 text-sm text-muted">{subtitle}</p>
        </div>
        {children}
      </div>
    </main>
  );
}

/** Chat bubble: child/user on the right, bot on the left. `testid` sits on the text element only. */
export function Bubble({ role, children, testid }: { role: "user" | "bot"; children: ReactNode; testid?: string }) {
  const user = role === "user";
  return (
    <div className={cx("flex items-end gap-2", user ? "justify-end" : "justify-start")}>
      {!user && <span aria-hidden="true" className="mb-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-full bg-brand-100 font-hanja text-sm font-bold text-brand-800 dark:bg-brand-900 dark:text-brand-200">漢</span>}
      <div data-testid={testid}
        className={cx("max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-[15px] leading-relaxed sm:max-w-[75%]",
          user ? "rounded-br-md bg-brand-700 text-white" : "rounded-bl-md border border-line bg-soft text-ink")}>
        {children}
      </div>
    </div>
  );
}
