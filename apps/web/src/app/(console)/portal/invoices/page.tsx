"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import {
  Badge, Card, EmptyState, ErrorState, Field, MoneyCell, Modal, PageHeader, Spinner,
  StatusPill, Table, TBody, TD, TH, THead, TR, Textarea, Input, Button,
} from "@cloudpartnerops/ui";

interface PortalInvoice {
  id: string; invoice_number: string; total: string; currency: string;
  period_start: string; period_end: string; status: string;
  issued_at: string | null; due_date: string | null; notes_customer: string | null;
}

export default function PortalInvoicesPage() {
  const [rows, setRows] = useState<PortalInvoice[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [disputeFor, setDisputeFor] = useState<PortalInvoice | null>(null);

  useEffect(() => {
    api.get<PortalInvoice[]>("/api/v1/portal/invoices")
      .then(setRows).catch((e) => setError(String(e?.message ?? e)));
  }, []);

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Invoices" subtitle="Billing statements issued to you. Questions? File a dispute on any invoice." />
      {error ? <ErrorState detail={error} /> : !rows ? <Spinner label="Loading invoices" /> : rows.length === 0 ? (
        <Card className="p-6"><EmptyState title="No invoices yet" hint="Your first statement will appear here once issued." /></Card>
      ) : (
        <Card>
          <Table>
            <THead><TR><TH>Invoice</TH><TH>Period</TH><TH className="text-right">Total</TH><TH>Status</TH><TH>Due</TH><TH /></TR></THead>
            <TBody>
              {rows.map((i) => (
                <TR key={i.id}>
                  <TD>
                    <Link href={`/portal/invoices/${i.id}`} className="cpo-focus rounded font-mono text-xs underline-offset-2 hover:underline">{i.invoice_number}</Link>
                  </TD>
                  <TD className="text-xs tabular-nums">{i.period_start.slice(0, 10)} → {i.period_end.slice(0, 10)}</TD>
                  <TD className="text-right"><MoneyCell value={i.total} currency={i.currency} /></TD>
                  <TD><StatusPill status={i.status} /></TD>
                  <TD className="text-xs tabular-nums">{i.due_date ? i.due_date.slice(0, 10) : "—"}</TD>
                  <TD className="text-right">
                    <Button size="sm" variant="ghost" onClick={() => setDisputeFor(i)}>Dispute</Button>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </Card>
      )}
      {disputeFor && <DisputeModal invoice={disputeFor} onClose={() => setDisputeFor(null)} />}
    </div>
  );
}

function DisputeModal({ invoice, onClose }: { invoice: PortalInvoice; onClose: () => void }) {
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true); setError(null);
    try {
      const r = await api.post<{ dispute_number: string }>("/api/v1/disputes", {
        invoice_id: invoice.id, subject, description,
        amount_disputed: amount || null,
      });
      setDone(r.dispute_number);
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not file dispute");
    } finally { setBusy(false); }
  }

  return (
    <Modal open title={`Dispute invoice ${invoice.invoice_number}`} onClose={onClose}>
      {done ? (
        <div className="space-y-3">
          <p className="text-sm">Dispute <Badge tone="info">{done}</Badge> filed. Your partner will respond here and on your next statement.</p>
          <div className="text-right"><Button onClick={onClose}>Close</Button></div>
        </div>
      ) : (
        <div className="space-y-4">
          <Field label="Subject" required>
          { (id) => <Input id={id} value={subject} onChange={(e) => setSubject(e.target.value)} required minLength={4} /> }
        </Field>
          <Field label="What looks wrong?">
          { (id) => <Textarea id={id} value={description} onChange={(e) => setDescription(e.target.value)} /> }
        </Field>
          <Field label="Amount disputed (optional)">
          { (id) => <Input id={id} value={amount} onChange={(e) => setAmount(e.target.value)} placeholder={invoice.total} /> }
        </Field>
          {error && <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-xs text-negative ring-1 ring-red-200">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={onClose}>Cancel</Button>
            <Button loading={busy} onClick={submit} disabled={subject.trim().length < 4}>File dispute</Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
