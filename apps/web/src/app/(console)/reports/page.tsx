"use client";

import { useEffect, useState } from "react";
import { useApp } from "@/lib/app-state";
import {
  createSchedule, fetchReportCatalog, fetchSchedules, reportDownloadUrl, toggleSchedule,
  type ReportDef, type ScheduleRow,
} from "@/lib/ops-api";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, ErrorState, Field, Input, Modal,
  PageHeader, Select, Spinner,
} from "@cloudpartnerops/ui";

const PERIODS = [
  { label: "All periods", value: "" },
  { label: "June 2026", value: "2026-06-01T00:00:00+00:00" },
  { label: "July 2026", value: "2026-07-01T00:00:00+00:00" },
  { label: "August 2026", value: "2026-08-01T00:00:00+00:00" },
];

export default function ReportsPage() {
  const { me } = useApp();
  const canRead = me?.permissions.includes("report.read") ?? false;
  const canSchedule = me?.permissions.includes("report.schedule") ?? false;
  const [catalog, setCatalog] = useState<ReportDef[] | null>(null);
  const [schedules, setSchedules] = useState<ScheduleRow[] | null>(null);
  const [schedOpen, setSchedOpen] = useState(false);
  const [period, setPeriod] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);

  const loadSchedules = () => {
    fetchSchedules().then(setSchedules).catch(() => setSchedules([]));
  };

  useEffect(() => {
    if (!canRead) return;
    fetchReportCatalog().then(setCatalog).catch((e) => setError(String(e?.message ?? e)));
    loadSchedules();
  }, [canRead]);

  if (!canRead) {
    return (
      <div className="mx-auto max-w-6xl">
        <PageHeader title="Reports" />
        <Card className="p-6 text-sm text-ink-500">Your role does not include report access.</Card>
      </div>
    );
  }

  async function download(key: string) {
    setDownloading(key); setError(null);
    try {
      const res = await fetch(reportDownloadUrl(key, period || undefined), { credentials: "include" });
      if (!res.ok) throw new Error(`report failed (${res.status})`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `${key}.csv`; a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "download failed");
    } finally { setDownloading(null); }
  }

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Reports"
        subtitle="On-demand CSV reports over the same data the dashboards use — a report and its dashboard can never disagree. Every export is audited."
        filters={
          <Select aria-label="Period" className="w-52" value={period} onChange={(e) => setPeriod(e.target.value)}>
            {PERIODS.map((p) => <option key={p.label} value={p.value}>{p.label}</option>)}
          </Select>
        }
      />
      {error && <Card className="mb-4 p-4"><ErrorState detail={error} /></Card>}
      {!catalog ? <Spinner label="Loading catalog" /> : (
        <div className="grid gap-3 sm:grid-cols-2">
          {catalog.map((r) => (
            <Card key={r.key}>
              <CardBody className="flex items-center justify-between gap-4 px-5 py-4">
                <div>
                  <div className="font-display text-sm font-semibold">{r.key.replace(/_/g, " ")}</div>
                  <p className="mt-0.5 text-xs text-ink-500">{r.description}</p>
                </div>
                <Button size="sm" variant="secondary" loading={downloading === r.key}
                        disabled={downloading !== null} onClick={() => void download(r.key)}>
                  Download CSV
                </Button>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      <Card className="mt-6">
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Scheduled reports</CardTitle>
            {canSchedule && <Button size="sm" onClick={() => setSchedOpen(true)}>New schedule</Button>}
          </div>
        </CardHeader>
        <CardBody className="p-0">
          {!schedules ? <div className="p-5"><Spinner label="Loading schedules" /></div> :
            schedules.length === 0 ? (
            <p className="px-5 py-4 text-sm text-ink-500">
              Nothing scheduled. A schedule generates its report on cadence, stores the CSV in object
              storage as an audited export, and queues an email per recipient through the notification
              outbox.
            </p>
          ) : (
            <ul className="divide-y divide-ink-100">
              {schedules.map((x) => (
                <li key={x.id} className="flex items-center justify-between gap-3 px-5 py-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{x.name}</span>
                      <Badge tone="neutral">{x.report_key.replace(/_/g, " ")}</Badge>
                      {!x.enabled && <Badge tone="warning">paused</Badge>}
                    </div>
                    <p className="mt-0.5 text-xs text-ink-500">
                      {x.cadence === "monthly"
                        ? `Monthly on day ${x.day_of_month} at ${String(x.hour_utc).padStart(2, "0")}:00 UTC`
                        : `Weekly on ${["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][x.day_of_week]} ${String(x.hour_utc).padStart(2, "0")}:00 UTC`}
                      {" · "}covers month −{x.period_offset_months}
                      {x.recipients.length > 0 && <> · {x.recipients.join(", ")}</>}
                      {x.next_run_at && <> · next {new Date(x.next_run_at).toLocaleString()}</>}
                    </p>
                  </div>
                  {canSchedule && (
                    <Button size="sm" variant="secondary" onClick={async () => {
                      await toggleSchedule(x.id);
                      loadSchedules();
                    }}>
                      {x.enabled ? "Pause" : "Resume"}
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      <p className="mt-4 text-xs text-ink-400">
        Email transport is an integration boundary (Phase 5): outbox rows are real and auditable, live
        SMTP/webhook delivery is not yet wired. PDF report layout arrives with the Phase 5 reporting service.
      </p>

      {schedOpen && catalog && (
        <ScheduleModal reports={catalog} onClose={() => setSchedOpen(false)}
                       onDone={() => { setSchedOpen(false); loadSchedules(); }} />
      )}
    </div>
  );
}

const DOW = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function ScheduleModal({ reports, onClose, onDone }: {
  reports: ReportDef[]; onClose: () => void; onDone: () => void;
}) {
  const [name, setName] = useState("");
  const [key, setKey] = useState(reports[0]?.key ?? "");
  const [cadence, setCadence] = useState("monthly");
  const [dom, setDom] = useState("1");
  const [dow, setDow] = useState("1");
  const [hour, setHour] = useState("6");
  const [recipients, setRecipients] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      await createSchedule({
        name, report_key: key, cadence,
        day_of_month: Number(dom), day_of_week: Number(dow), hour_utc: Number(hour),
        recipients: recipients.split(",").map((s) => s.trim()).filter(Boolean),
      });
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed");
    } finally { setBusy(false); }
  }

  return (
    <Modal open title="Schedule a report" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Name" required>
          {(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} required minLength={2} />}
        </Field>
        <Field label="Report">
          {(id) => (
            <Select id={id} value={key} onChange={(e) => setKey(e.target.value)}>
              {reports.map((r) => <option key={r.key} value={r.key}>{r.key.replace(/_/g, " ")}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Cadence">
          {(id) => (
            <Select id={id} value={cadence} onChange={(e) => setCadence(e.target.value)}>
              <option value="monthly">Monthly</option>
              <option value="weekly">Weekly</option>
            </Select>
          )}
        </Field>
        <div className="grid grid-cols-2 gap-3">
          {cadence === "monthly" ? (
            <Field label="Day of month (1–28)">
              {(id) => <Input id={id} type="number" min={1} max={28} value={dom} onChange={(e) => setDom(e.target.value)} required />}
            </Field>
          ) : (
            <Field label="Day of week">
              {(id) => (
                <Select id={id} value={dow} onChange={(e) => setDow(e.target.value)}>
                  {DOW.map((d, i) => <option key={d} value={i}>{d}</option>)}
                </Select>
              )}
            </Field>
          )}
          <Field label="Hour (UTC)">
            {(id) => <Input id={id} type="number" min={0} max={23} value={hour} onChange={(e) => setHour(e.target.value)} required />}
          </Field>
        </div>
        <Field label="Recipients" hint="Comma-separated emails. Each run queues one outbox notification per recipient.">
          {(id) => <Input id={id} value={recipients} onChange={(e) => setRecipients(e.target.value)} placeholder="finops@partner.example.com" />}
        </Field>
        {error && <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-xs text-negative ring-1 ring-red-200">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="submit" loading={busy}>Create schedule</Button>
        </div>
      </form>
    </Modal>
  );
}
