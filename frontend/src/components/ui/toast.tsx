"use client";

import * as React from "react";
import { AnimatePresence, motion } from "framer-motion";
import { AlertCircle, AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import { cn } from "@/lib/cn";

type ToastTone = "success" | "danger" | "info" | "warning";
interface ToastItem {
  id: number;
  title: string;
  description?: string;
  tone: ToastTone;
}

const Ctx = React.createContext<{ toast: (t: Omit<ToastItem, "id" | "tone"> & { tone?: ToastTone }) => void } | null>(null);

export function useToast() {
  const c = React.useContext(Ctx);
  if (!c) throw new Error("useToast must be used inside <ToastProvider>");
  return c.toast;
}

const icons = { success: CheckCircle2, danger: AlertCircle, info: Info, warning: AlertTriangle };
const iconTone = { success: "text-success", danger: "text-danger", info: "text-info", warning: "text-warning" };

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = React.useState<ToastItem[]>([]);
  const seq = React.useRef(0);

  const dismiss = React.useCallback((id: number) => setItems((l) => l.filter((t) => t.id !== id)), []);
  const toast = React.useCallback<NonNullable<React.ContextType<typeof Ctx>>["toast"]>(
    (t) => {
      const id = ++seq.current;
      setItems((l) => [...l.slice(-3), { id, tone: "info", ...t }]);
      window.setTimeout(() => dismiss(id), 5000);
    },
    [dismiss],
  );

  return (
    <Ctx.Provider value={{ toast }}>
      {children}
      <div
        aria-live="polite"
        aria-atomic="false"
        className="pointer-events-none fixed right-4 bottom-4 z-[80] flex w-[min(92vw,360px)] flex-col gap-2"
      >
        <AnimatePresence initial={false}>
          {items.map((t) => {
            const Icon = icons[t.tone];
            return (
              <motion.div
                key={t.id}
                layout
                initial={{ opacity: 0, y: 12, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, x: 24 }}
                transition={{ duration: 0.2 }}
                role="status"
                className="pointer-events-auto flex gap-3 rounded-lg border border-border bg-elevated p-3 shadow-lg"
              >
                <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", iconTone[t.tone])} aria-hidden />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-text">{t.title}</p>
                  {t.description && <p className="mt-0.5 text-xs text-muted">{t.description}</p>}
                </div>
                <button
                  aria-label="Dismiss notification"
                  onClick={() => dismiss(t.id)}
                  className="h-6 w-6 shrink-0 rounded text-faint hover:bg-subtle hover:text-text"
                >
                  <X className="mx-auto h-3.5 w-3.5" />
                </button>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </Ctx.Provider>
  );
}
