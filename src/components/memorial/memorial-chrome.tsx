"use client";

import type { ReactNode } from "react";
import { motion, type HTMLMotionProps } from "framer-motion";

export function MemorialIconButton({
  children,
  className = "",
  ...props
}: HTMLMotionProps<"button"> & { children: ReactNode }) {
  return (
    <motion.button
      type="button"
      className={`mem-icon-btn ${className}`}
      whileTap={{ scale: 0.95 }}
      {...props}
    >
      {children}
    </motion.button>
  );
}

export function MemorialPrimaryButton({
  children,
  className = "",
  disabled,
  ...props
}: HTMLMotionProps<"button"> & { children: ReactNode }) {
  return (
    <motion.button
      type="button"
      disabled={disabled}
      className={`mem-btn-primary ${className}`}
      whileTap={disabled ? undefined : { scale: 0.98 }}
      {...props}
    >
      {children}
    </motion.button>
  );
}
