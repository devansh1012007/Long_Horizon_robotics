// Typed client for the engine sidecar + Prisma write-through sync.
import { db } from "@/lib/db";
import { ensureEngine } from "@/lib/engine-supervisor";

const BASE = process.env.ENGINE_URL ?? "http://127.0.0.1:3031";

export type JobSpec = {
  systems: string[];
  horizons: number[];
  condition: string;
  fault: string | null;
  episodes: number;
  base_seed: number;
  backend?: string;
  label: string;
};

export type SystemSummary = {
  n: number;
  successes?: number;
  success_rate?: number;
  ci_lo?: number;
  ci_hi?: number;
  extra_actions_mean?: number;
  recovery_time_mean?: number;
  recovery_n?: number;
  diag_n?: number;
  diag_accuracy?: number | null;
  escalations?: number;
  interventions?: number;
  unsafe_executions?: number;
  false_alarm_episode_rate?: number;
};

export type PairedStat = {
  vs: string;
  n_pairs: number;
  delta_success_mean: number;
  delta_ci_lo: number;
  delta_ci_hi: number;
  ci_excludes_zero: boolean;
  mcnemar_p: number;
  wilcoxon_p: number;
};

export type EpisodeRowT = {
  system: string;
  condition: string;
  horizon: number;
  seed: number;
  ep_id: string;
  outcome: string;
  success: number;
  extra_actions: number;
  recovery_time_s: number;
  vetoes: number;
  unsafe_executions: number;
  interventions: number;
  escalations: number;
  flags: number;
  flags_fault_free: number;
  steps: number;
  diag_correct: number | null;
  regret: number;
  fault: string;
  task_id: string;
  backend?: string;
};

export type EngineJob = {
  id: string;
  user_id: string;
  label: string;
  spec: JobSpec;
  status: "queued" | "running" | "done" | "failed";
  progress: number;
  total: number;
  created: number;
  started: number | null;
  finished: number | null;
  error: string | null;
  summary: Record<string, SystemSummary> | null;
  stats: PairedStat[] | null;
  rows?: EpisodeRowT[];
};

export class EngineError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function call<T>(
  path: string,
  init?: RequestInit & { timeoutMs?: number },
): Promise<T> {
  const ok = await ensureEngine();
  if (!ok) {
    throw new EngineError(
      503,
      "Engine service is starting — try again in a few seconds.",
    );
  }
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), init?.timeoutMs ?? 15000);
  try {
    const res = await fetch(`${BASE}${path}`, {
      ...init,
      signal: ctl.signal,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new EngineError(
        res.status,
        (body as { detail?: string })?.detail ?? `engine ${res.status}`,
      );
    }
    return body as T;
  } catch (e) {
    if (e instanceof EngineError) throw e;
    throw new EngineError(502, `Engine unreachable: ${String(e)}`);
  } finally {
    clearTimeout(t);
  }
}

export const engine = {
  capabilities: () =>
    call<{
      systems: {
        id: string;
        name: string;
        family: string;
        desc: string;
      }[];
      faults: string[];
      fault_labels: Record<string, string>;
      conditions: string[];
      horizons: number[];
      limits: Record<string, number>;
    }>("/capabilities", { timeoutMs: 4000 }),

  health: () =>
    call<{
      status: string;
      version: string;
      uptime_s: number;
      pool: { workers: number; active: number; queued: number };
      jobs: Record<string, number>;
      episodes_recorded: number;
      limits: Record<string, number>;
    }>("/health", { timeoutMs: 4000 }),

  submit: (userId: string, spec: JobSpec) =>
    call<{ job_id: string; status: string; total: number }>("/jobs", {
      method: "POST",
      body: JSON.stringify({ user_id: userId, spec }),
    }),

  listJobs: (userId?: string) =>
    call<{ jobs: EngineJob[] }>(
      `/jobs${userId ? `?user_id=${encodeURIComponent(userId)}` : ""}`,
    ),

  getJob: (jobId: string) => call<EngineJob>(`/jobs/${jobId}`),

  getResult: (jobId: string) => call<EngineJob>(`/jobs/${jobId}/result`),

  episodeDetail: (jobId: string, epId: string) =>
    call<{
      episode: Record<string, unknown>;
      metrics: Record<string, unknown>;
      events: { t: number; node: string; etype: string; payload: Record<string, unknown> }[];
      diagnoses: Record<string, unknown>[];
      recoveries: Record<string, unknown>[];
    }>(`/jobs/${jobId}/episodes/${encodeURIComponent(epId)}`),

  deleteJob: (jobId: string) =>
    call<{ deleted: string }>(`/jobs/${jobId}`, { method: "DELETE" }),
};

/** Persist finished engine job results into Prisma (idempotent). */
export async function syncJobToDb(job: EngineJob): Promise<void> {
  const existing = await db.job.findUnique({
    where: { id: job.id },
    select: { status: true },
  });
  const patch = {
    status: job.status,
    progress: job.progress,
    total: job.total,
    summary: job.summary ? JSON.stringify(job.summary) : null,
    stats: job.stats ? JSON.stringify(job.stats) : null,
    error: job.error ? job.error.slice(0, 2000) : null,
    finishedAt: job.finished ? new Date(job.finished * 1000) : null,
  };
  if (!existing) {
    await db.job.create({
      data: {
        id: job.id,
        userId: job.user_id,
        label: job.label,
        spec: JSON.stringify(job.spec),
        ...patch,
      },
    });
  } else {
    await db.job.update({ where: { id: job.id }, data: patch });
  }
  if (job.status === "done" && job.rows?.length) {
    const already = await db.episodeRow.count({
      where: { jobId: job.id },
    });
    if (already === 0) {
      const created = job.rows.map((r) => ({
        jobId: job.id,
        userId: job.user_id,
        epId: r.ep_id,
        system: r.system,
        condition: r.condition,
        horizon: r.horizon,
        seed: r.seed,
        fault: r.fault,
        outcome: r.outcome,
        success: !!r.success,
        extraActions: r.extra_actions,
        recoveryTimeS: r.recovery_time_s,
        diagCorrect: r.diag_correct,
        escalations: r.escalations,
        interventions: r.interventions,
        unsafeExec: r.unsafe_executions,
        falseAlarms: r.flags_fault_free,
      }));
      // chunk inserts to stay within SQLite parameter limits
      for (let i = 0; i < created.length; i += 60) {
        await db.episodeRow.createMany({ data: created.slice(i, i + 60) });
      }
    }
  }
}
