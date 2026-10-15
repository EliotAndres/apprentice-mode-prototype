// Small building blocks shared by both tabs.
import type { ComponentProps, ReactNode } from "react";

type Children = { children?: ReactNode; className?: string };
type Choice<T extends string> = { options: (readonly [T, ReactNode])[]; value: T; onChange: (value: T) => void };

export const Swatch = ({ color, className = "" }: { color: string; className?: string }) =>
  <span className={`inline-block size-2.5 flex-none rounded-[2px] ${className}`} style={{ background: color }} />;

export const SectionHead = ({ children }: Children) =>
  <h2 className="mb-4 flex items-center gap-3 font-serif text-2xl font-medium leading-tight tracking-[-0.005em] sm:text-[30px]
    before:size-5 before:flex-none before:bg-black">{children}</h2>;

// Vertical padding comes from the caller.
export const Card = ({ className = "", children }: Children) =>
  <div className={`rounded-lg border border-line-strong bg-white px-3.5 sm:px-5 ${className}`}>{children}</div>;

export const Segmented = <T extends string>({ options, value, onChange }: Choice<T>) =>
  <div className="inline-flex gap-0.5 rounded-md border border-line bg-chip p-0.5">
    {options.map(([key, label]) =>
      <button key={key} aria-pressed={value === key} onClick={() => onChange(key)}
        className="h-7 cursor-pointer rounded border border-transparent px-3 text-xs font-medium hover:bg-white
          aria-pressed:border-line aria-pressed:bg-white">{label}</button>)}
  </div>;

const CHEVRONS = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%23787878' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m7 15 5 5 5-5M7 9l5-5 5 5'/%3E%3C/svg%3E\")";
export const Select = <T extends string>({ options, value, onChange, label }: Choice<T> & { label: string }) =>
  <select aria-label={label} value={value} onChange={event => onChange(event.target.value as T)}
    style={{ backgroundImage: CHEVRONS }}
    className="h-8 max-w-full cursor-pointer appearance-none rounded-lg border border-line bg-band bg-[right_10px_center] bg-no-repeat
      pl-3 pr-[34px] text-[13px] text-black hover:border-secondary sm:min-w-[220px]">
    {options.map(([key, name]) => <option key={key} value={key}>{name}</option>)}
  </select>;

// `path` is always a constant in this file or a caller, never data.
export const Icon = ({ path, className = "size-4", strokeWidth = 2 }: { path: string; className?: string; strokeWidth?: number }) =>
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round"
    strokeLinejoin="round" className={className} dangerouslySetInnerHTML={{ __html: path }} />;

export const IconButton = ({ label, path, onClick }: { label: string; path: string; onClick: () => void }) =>
  <button title={label} aria-label={label} onClick={onClick}
    className="inline-flex size-8 cursor-pointer items-center justify-center rounded-md border border-line bg-white hover:bg-chip">
    <Icon path={path} />
  </button>;

// The grey "+" row that opens to explain something, like the reference's "What This Metric Means".
export const Explain = ({ title, children, className = "", ...props }: ComponentProps<"details">) =>
  <details className={`group border-b border-line ${className}`} {...props}>
    <summary className="flex cursor-pointer list-none items-center gap-1.5 py-2 text-xs text-faint
      after:ml-auto after:text-xl after:font-light after:leading-none after:text-muted after:content-['+'] group-open:after:content-['−']">
      <Icon className="size-[13px]" path='<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>' />{title}
    </summary>
    {children}
  </details>;
