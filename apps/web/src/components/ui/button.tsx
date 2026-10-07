import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import type { ButtonHTMLAttributes } from "react"

import { cn } from "../../lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-xl text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-600/35 disabled:pointer-events-none disabled:opacity-45",
  {
    variants: {
      variant: {
        default: "bg-slate-900 px-4 py-2.5 text-white shadow-sm hover:bg-slate-800",
        secondary: "border border-slate-200 bg-white px-4 py-2.5 text-slate-700 shadow-sm hover:bg-slate-50",
        ghost: "px-3 py-2 text-slate-600 hover:bg-slate-100 hover:text-slate-950",
        danger: "bg-rose-50 px-4 py-2.5 text-rose-700 hover:bg-rose-100",
      },
      size: { default: "h-10", sm: "h-8 text-xs", icon: "size-10" },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
)

type Props = ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants> & { asChild?: boolean }

export function Button({ className, variant, size, asChild, ...props }: Props) {
  const Component = asChild ? Slot : "button"
  return <Component className={cn(buttonVariants({ variant, size }), className)} {...props} />
}
