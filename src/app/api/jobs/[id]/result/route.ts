import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { currentUser, unauthorized } from "@/lib/session";
import { EngineError, engine, syncJobToDb } from "@/lib/engine-client";
import { rateLimit, RULES } from "@/lib/rate-limit";

export async function GET(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const user = await currentUser(req);
  if (!user) return unauthorized();
  const rl = rateLimit(`heavy:${user.id}`, RULES.heavy);
  if (!rl.ok) {
    return NextResponse.json({ error: "Rate limit." }, { status: 429 });
  }
  const { id } = await params;
  const owned = await db.job.findUnique({
    where: { id },
    select: { userId: true },
  });
  if (!owned || owned.userId !== user.id) {
    return NextResponse.json({ error: "Job not found." }, { status: 404 });
  }
  try {
    const job = await engine.getResult(id);
    await syncJobToDb(job);
    return NextResponse.json({
      id: job.id,
      label: job.label,
      spec: job.spec,
      status: job.status,
      progress: job.progress,
      total: job.total,
      summary: job.summary,
      stats: job.stats,
      rows: job.rows ?? [],
    });
  } catch (e) {
    if (e instanceof EngineError && e.status === 404) {
      // engine pruned it: serve rows from Prisma
      const job = await db.job.findUnique({
        where: { id },
        include: { episodes: true },
      });
      if (job) {
        return NextResponse.json({
          id: job.id,
          label: job.label,
          spec: JSON.parse(job.spec || "{}"),
          status: job.status,
          progress: job.progress,
          total: job.total,
          summary: job.summary ? JSON.parse(job.summary) : null,
          stats: job.stats ? JSON.parse(job.stats) : null,
          rows: job.episodes.map((r) => ({
            system: r.system,
            condition: r.condition,
            horizon: r.horizon,
            seed: r.seed,
            ep_id: r.epId,
            outcome: r.outcome,
            success: r.success ? 1 : 0,
            extra_actions: r.extraActions,
            recovery_time_s: r.recoveryTimeS,
            escalations: r.escalations,
            interventions: r.interventions,
            unsafe_executions: r.unsafeExec,
            flags_fault_free: r.falseAlarms,
            diag_correct: r.diagCorrect,
            fault: r.fault,
            task_id: "",
          })),
        });
      }
    }
    if (e instanceof EngineError) {
      return NextResponse.json({ error: e.message }, { status: e.status });
    }
    return NextResponse.json({ error: "Engine unreachable." }, { status: 503 });
  }
}
