import type { InputHTMLAttributes } from "react"

import { cn } from "../../lib/utils"

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-11 w-full rounded-xl border border-white/10 bg-slate-950/65 px-3 text-sm text-white outline-none placeholder:text-slate-500 focus:border-cyan-400/70 focus:ring-2 focus:ring-cyan-400/15",
        className,
      )}
      {...props}
    />
  )
}
