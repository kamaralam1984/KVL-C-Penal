"use client";

import * as React from "react";
import { Bot, LifeBuoy } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useApi } from "@/hooks/use-api";
import { ApiError, post } from "@/lib/http";

interface Answer { answer: string; sources: { slug: string; title: string }[]; handoff: boolean }

/** Ask AI first; if it cannot help, the question goes to a human as a ticket. */
export function SupportAiBox({ onTicket }: { onTicket?: () => void }) {
  const st = useApi<{ state: string }>("/ai/status");
  const [q, setQ] = React.useState("");
  const [a, setA] = React.useState<Answer | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState<string>();
  const [ticket, setTicket] = React.useState<string>();
  if (st.data?.state !== "configured") return null;

  async function createTicket() {
    setBusy(true);
    try {
      const t = await post<{ number: number }>("/support/tickets", { subject: q.slice(0, 120), body: `${q}\n\n---\nAI assistant could not resolve this${a ? `:\n${a.answer}` : "."}`, category: "general", priority: "normal" });
      setTicket(`Ticket #${t.number} created. Our team will reply soon.`);
      onTicket?.();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not create the ticket.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="mb-4">
      <CardHeader title="Ask AI" description="Get an instant answer from our help articles. If it can't help, a person will." actions={<Bot className="h-5 w-5 text-primary" aria-hidden />} />
      <CardBody className="space-y-3">
        <form className="flex gap-2" onSubmit={async (e) => {
          e.preventDefault();
          if (q.trim().length < 3) return;
          setBusy(true); setErr(undefined); setA(null); setTicket(undefined);
          try { setA(await post<Answer>("/support/ai/answer", { question: q.trim() })); } catch (x) { setErr(x instanceof ApiError ? x.message : "No answer."); } finally { setBusy(false); }
        }}>
          <input aria-label="Your question" value={q} maxLength={2000} onChange={(e) => setQ(e.target.value)} placeholder="e.g. How do I add SSL to my domain?" className="min-w-0 flex-1 rounded-md border border-border bg-surface px-3 py-2 text-sm text-text" />
          <Button type="submit" loading={busy && !a}>Ask</Button>
        </form>
        {err && <p role="alert" className="text-sm text-danger">{err}</p>}
        {a && (
          <div className="space-y-2 rounded-lg bg-subtle p-3 text-sm">
            <p className="whitespace-pre-wrap text-text">{a.answer}</p>
            {!!a.sources.length && <p className="text-xs text-muted">From: {a.sources.map((s) => s.title).join(", ")}</p>}
            {ticket ? <p role="status" className="text-sm text-success">{ticket}</p> : (
              <Button size="sm" variant={a.handoff ? "primary" : "outline"} loading={busy} onClick={() => void createTicket()}><LifeBuoy className="h-4 w-4" />{a.handoff ? "Send to a support person" : "Still need help? Talk to a person"}</Button>
            )}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
