"use client";

import * as React from "react";
import { BarChart3, Bot, Search, Send, Sparkles, Wrench } from "lucide-react";
import { PageHeader } from "@/components/shell/page-header";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useApi } from "@/hooks/use-api";
import { ApiError, post } from "@/lib/http";

interface Msg { role: "user" | "assistant"; content: string; tools?: string[] }
const STARTERS = [
  { icon: BarChart3, title: "Report", text: "Summarise server health, open alerts and backup status right now." },
  { icon: Search, title: "Investigate", text: "Which customer accounts are close to their quota limits?" },
  { icon: Wrench, title: "Troubleshoot", text: "Are there urgent support tickets or SLA breaches I should look at first?" },
];

/** Ask the panel in plain language. Read-only: the assistant looks data up with your permissions and never changes anything. */
export function AssistantPage() {
  const st = useApi<{ state: string; message?: string; model?: string; assistant_tools: string[] }>("/ai/status");
  const [msgs, setMsgs] = React.useState<Msg[]>([]);
  const [input, setInput] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState<string>();
  const end = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => end.current?.scrollIntoView({ behavior: "smooth", block: "end" }), [msgs, busy]);

  async function ask(q: string) {
    const question = q.trim();
    if (!question || busy) return;
    const next: Msg[] = [...msgs, { role: "user", content: question }];
    setMsgs(next);
    setInput("");
    setBusy(true);
    setErr(undefined);
    try {
      const r = await post<{ answer: string; tools_used: string[] }>("/ai/assistant", { messages: next.slice(-20).map(({ role, content }) => ({ role, content: content.slice(0, 4000) })) });
      setMsgs([...next, { role: "assistant", content: r.answer, tools: r.tools_used }]);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "The assistant did not answer.");
      setMsgs(msgs);
      setInput(question);
    } finally {
      setBusy(false);
    }
  }

  const off = st.data && st.data.state !== "configured";
  return (
    <>
      <PageHeader title="AI assistant" description="Ask about servers, alerts, backups, tickets, quotas and websites in plain language. It reads data with your permissions and never changes anything." />
      {off && <p className="mb-4 rounded-md bg-warning/10 px-3 py-2 text-sm text-warning">AI is not configured: {st.data?.message}</p>}
      {msgs.length === 0 && (
        <div className="mb-4 grid gap-3 sm:grid-cols-3">
          {STARTERS.map((s) => (
            <button key={s.title} disabled={!!off || busy} onClick={() => void ask(s.text)} className="group rounded-xl border border-border bg-surface p-4 text-left transition hover:border-primary hover:shadow-[0_0_0_3px_var(--primary-soft)] disabled:opacity-50">
              <s.icon className="mb-2 h-6 w-6 text-primary" aria-hidden />
              <p className="font-semibold text-text">{s.title}</p>
              <p className="mt-1 text-sm text-muted">{s.text}</p>
            </button>
          ))}
        </div>
      )}
      <Card>
        <CardHeader title="Conversation" description={st.data?.model ? `Model: ${st.data.model}` : undefined} actions={<Sparkles className="h-5 w-5 text-primary" aria-hidden />} />
        <CardBody className="space-y-3">
          <div className="max-h-[55vh] space-y-3 overflow-y-auto" aria-live="polite">
            {msgs.map((m, i) => (
              <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm whitespace-pre-wrap ${m.role === "user" ? "bg-primary text-[var(--primary-fg,white)]" : "bg-subtle text-text"}`}>
                  {m.role === "assistant" && <Bot className="mb-1 h-4 w-4 text-primary" aria-label="Assistant" />}
                  {m.content}
                  {!!m.tools?.length && <p className="mt-2 text-[11px] text-muted">Looked up: {[...new Set(m.tools)].join(", ").replace(/_/g, " ")}</p>}
                </div>
              </div>
            ))}
            {busy && <p role="status" className="text-sm text-muted">Looking it up…</p>}
            <div ref={end} />
          </div>
          {err && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">{err}</p>}
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void ask(input); }}>
            <textarea aria-label="Your question" rows={2} maxLength={4000} value={input} disabled={!!off} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void ask(input); } }} placeholder="e.g. Why was the panel slow yesterday evening?" className="min-w-0 flex-1 resize-none rounded-md border border-border bg-surface p-2 text-sm text-text" />
            <Button type="submit" loading={busy} disabled={!input.trim() || !!off}><Send className="h-4 w-4" /><span className="sr-only">Send</span></Button>
          </form>
          {msgs.length > 0 && <Button size="sm" variant="ghost" onClick={() => setMsgs([])}>New conversation</Button>}
        </CardBody>
      </Card>
    </>
  );
}
