"use client";

import { useCallback, useEffect, useState } from "react";
import { useApp } from "@/lib/app-state";
import {
  askAssistant, assistantCapabilities, fetchAssistantAudit,
  type AiAuditRow, type AssistantAnswer, type AssistantCapabilities,
} from "@/lib/integrations-api";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, EmptyState, ErrorState, Input,
  PageHeader, Table, TBody, TD, TH, THead, TR,
} from "@cloudpartnerops/ui";

export function AssistantPage({ portal = false }: { portal?: boolean }) {
  const { me } = useApp();
  const canAsk = me?.permissions.includes("assistant.ask") ?? false;
  const canAudit = !portal && (me?.permissions.includes("audit.read") ?? false);
  const [caps, setCaps] = useState<AssistantCapabilities | null>(null);
  const [question, setQuestion] = useState("");
  const [answers, setAnswers] = useState<AssistantAnswer[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [audit, setAudit] = useState<AiAuditRow[] | null>(null);

  const load = useCallback(() => {
    if (!canAsk) return;
    assistantCapabilities().then(setCaps).catch(() => setCaps(null));
    if (canAudit) fetchAssistantAudit().then((r) => setAudit(r.items)).catch(() => setAudit([]));
  }, [canAsk, canAudit]);
  useEffect(load, [load]);

  async function ask(q?: string) {
    const text = (q ?? question).trim();
    if (text.length < 3 || busy) return;
    setBusy(true);
    setError(null);
    try {
      const a = await askAssistant(text);
      setAnswers((prev) => [{ ...a, question_text: text } as AssistantAnswer, ...prev].slice(0, 8));
      setQuestion("");
      if (canAudit) fetchAssistantAudit().then((r) => setAudit(r.items)).catch(() => undefined);
    } catch (e) {
      setError(e instanceof Error ? e.message : "the assistant request failed");
    } finally {
      setBusy(false);
    }
  }

  if (!canAsk) {
    return <EmptyState title="Assistant not available"
      hint="Your role does not include the assistant.ask permission." />;
  }

  const examples = (caps?.intents ?? [])
    .filter((i) => !i.partner_only || me?.permissions.includes("partner_data.view"))
    .slice(0, 6);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <PageHeader
        title="PartnerOps assistant"
        subtitle="Deterministic, permission-aware answers — computed live from the records your role can read."
      />

      <Card>
        <CardHeader>
          <CardTitle>Ask</CardTitle>
          <Badge tone="neutral">{caps?.mode ?? "deterministic_demo"}</Badge>
        </CardHeader>
        <CardBody className="space-y-3">
          <div className="flex gap-2">
            <form
              className="flex-1"
              onSubmit={(e) => { e.preventDefault(); void ask(); }}
            >
              <Input
                aria-label="Question"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="Why did Acme's invoice increase this month?"
              />
            </form>
            <Button onClick={() => void ask()} disabled={busy || question.trim().length < 3}>
              {busy ? "Thinking…" : "Ask"}
            </Button>
          </div>
          {examples.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {examples.map((i) => (
                <button
                  key={i.id}
                  type="button"
                  onClick={() => void ask(i.example)}
                  className="cpo-focus rounded-full border border-ink-200 px-3 py-1 text-xs text-ink-600 hover:border-ink-300 hover:text-ink-900"
                >
                  {i.example}
                </button>
              ))}
            </div>
          )}
          {error && <ErrorState detail={error} />}
        </CardBody>
      </Card>

      {answers.map((a, idx) => (
        <AnswerCard key={idx} a={a} q={(a as { question_text?: string }).question_text ?? ""} />
      ))}
      {answers.length === 0 && (
        <EmptyState title="No questions yet"
          hint="Answers cite the exact records they were computed from. Nothing here is generated." />
      )}

      {caps && (
        <Card>
          <CardHeader><CardTitle>Ground rules</CardTitle></CardHeader>
          <CardBody>
            <ul className="list-disc space-y-1 pl-5 text-sm text-ink-700">
              {caps.guarantees.map((g) => <li key={g}>{g}</li>)}
            </ul>
          </CardBody>
        </Card>
      )}

      {canAudit && audit && (
        <Card>
          <CardHeader><CardTitle>AI query audit ({audit.length})</CardTitle></CardHeader>
          <CardBody className="p-0">
            {audit.length === 0 ? (
              <div className="px-5 py-8 text-center text-sm text-ink-500">Nothing asked yet.</div>
            ) : (
              <Table>
                <THead><TR>
                  <TH>Question</TH><TH>Intent</TH><TH>Outcome</TH><TH>By</TH><TH>Latency</TH>
                </TR></THead>
                <TBody>
                  {audit.map((r) => (
                    <TR key={r.id}>
                      <TD className="max-w-xs truncate">{r.question}</TD>
                      <TD>{r.intent ?? "—"}</TD>
                      <TD>
                        {r.refused
                          ? <Badge tone="warning">refused{r.sensitive ? " · sensitive" : ""}</Badge>
                          : <Badge tone="positive">answered</Badge>}
                      </TD>
                      <TD className="whitespace-nowrap text-xs text-ink-600">{r.asked_by_email ?? "—"}</TD>
                      <TD>{r.latency_ms ?? "—"} ms</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </CardBody>
        </Card>
      )}
    </div>
  );
}

function AnswerCard({ a, q }: { a: AssistantAnswer; q: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          {q && <span className="block text-sm font-normal text-ink-500">Q: {q}</span>}
        </CardTitle>
        <div className="flex gap-2">
          <Badge tone="neutral">{a.intent}</Badge>
          {a.sensitive && <Badge tone="warning">sensitive · audited</Badge>}
        </div>
      </CardHeader>
      <CardBody className="space-y-3">
        <p className="whitespace-pre-wrap text-sm text-ink-900">{a.text}</p>
        {a.facts.length > 0 && (
          <div>
            <div className="mb-1 text-xs font-semibold uppercase text-ink-500">Facts</div>
            <Table>
              <TBody>
                {a.facts.map((f, i) => (
                  <TR key={i}>
                    <TD className="whitespace-nowrap text-xs font-medium text-ink-600">
                      {String(f["fact"] ?? "")}
                    </TD>
                    <TD className="text-xs">
                      {Object.entries(f).filter(([k]) => k !== "fact")
                        .map(([k, v]) => `${k}=${String(v)}`).join("  ·  ")}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </div>
        )}
        {a.estimates.length > 0 && (
          <div>
            <div className="mb-1 text-xs font-semibold uppercase text-amber-700">
              Estimates (not facts)
            </div>
            <ul className="list-disc pl-5 text-xs text-ink-700">
              {a.estimates.map((e, i) => <li key={i}>{JSON.stringify(e)}</li>)}
            </ul>
          </div>
        )}
        {a.citations.length > 0 && (
          <div>
            <div className="mb-1 text-xs font-semibold uppercase text-ink-500">Citations</div>
            <div className="flex flex-wrap gap-2">
              {a.citations.map((c, i) => (
                <span key={i} className="rounded border border-ink-200 px-2 py-0.5 text-xs text-ink-600">
                  {c.type}{c.ref ? `: ${c.ref}` : c.id ? `: ${String(c.id).slice(0, 8)}…` : ""}
                </span>
              ))}
            </div>
          </div>
        )}
        {a.refused && (
          <p className="rounded bg-ink-50 px-3 py-2 text-xs text-ink-600">
            Refused — {a.refusal_reason}
          </p>
        )}
        <div className="text-[11px] text-ink-400">
          mode: {a.mode} · {a.latency_ms} ms · tools: {a.tools_used.join(", ") || "none"}
        </div>
      </CardBody>
    </Card>
  );
}
