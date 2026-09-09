import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import type { ButtonHTMLAttributes } from "react"

import { cn } from "../../lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-xl text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400 disabled:pointer-events-none disabled:opacity-45",
  {
    variants: {
      variant: {
        default: "bg-cyan-400 px-4 py-2.5 text-slate-950 hover:bg-cyan-300",
        secondary: "bg-white/8 px-4 py-2.5 text-slate-100 hover:bg-white/13",
        ghost: "px-3 py-2 text-slate-300 hover:bg-white/7 hover:text-white",
        danger: "bg-rose-500/15 px-4 py-2.5 text-rose-200 hover:bg-rose-500/25",
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
