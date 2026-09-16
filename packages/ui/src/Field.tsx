"use client";

import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import { useId } from "react";
import { cn } from "./cn";

export function Field({
  label,
  hint,
  error,
  required,
  children,
  htmlFor,
}: {
  label: string;
  hint?: string;
  error?: string | null;
  required?: boolean;
  children: (id: string, describedBy: string | undefined) => ReactNode;
  htmlFor?: string;
}) {
  const autoId = useId();
  const id = htmlFor ?? autoId;
  const descId = hint || error ? `${id}-desc` : undefined;
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-xs font-medium text-ink-700">
        {label}
        {required && <span className="ml-0.5 text-negative" aria-hidden>*</span>}
      </label>
      {children(id, descId)}
      {error ? (
        <p id={descId} className="text-xs text-negative">
          {error}
        </p>
      ) : hint ? (
        <p id={descId} className="text-xs text-ink-400">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

const CONTROL =
  "w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm text-ink-950 placeholder:text-ink-300 " +
  "cpo-focus focus:border-brand-primary disabled:bg-ink-50 disabled:text-ink-400";

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(CONTROL, className)} {...rest} />;
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cn(CONTROL, "appearance-none pr-8", className)} {...rest}>
      {children}
    </select>
  );
}

export function Textarea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(CONTROL, "min-h-20", className)} {...rest} />;
}
