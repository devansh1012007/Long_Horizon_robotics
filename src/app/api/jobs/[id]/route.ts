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
  const rl = rateLimit(`read:${user.id}`, RULES.read);
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
    let job = await engine.getJob(id);
    // Write-through: a finished job's episode rows only ship with the
    // full result payload — fetch and persist them at the moment of
    // completion so the leaderboard/episode DB is never stale.
    if (job.status === "done" && !job.rows?.length) {
      try {
        job = await engine.getResult(id);
      } catch {
        // result fetch failed; keep the compact job payload
      }
    }
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
      error: job.error,
      createdAt: job.created,
      finishedAt: job.finished,
    });
  } catch (e) {
    if (e instanceof EngineError && e.status === 404) {
      const stored = await db.job.findUnique({ where: { id } });
      if (stored) {
        return NextResponse.json({
          id: stored.id,
          label: stored.label,
          spec: JSON.parse(stored.spec || "{}"),
          status: stored.status,
          progress: stored.progress,
          total: stored.total,
          summary: stored.summary ? JSON.parse(stored.summary) : null,
          stats: stored.stats ? JSON.parse(stored.stats) : null,
          error: stored.error,
          createdAt: stored.createdAt,
          finishedAt: stored.finishedAt,
        });
      }
    }
    if (e instanceof EngineError) {
      return NextResponse.json({ error: e.message }, { status: e.status });
    }
    return NextResponse.json({ error: "Engine unreachable." }, { status: 503 });
  }
}

export async function DELETE(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const user = await currentUser(req);
  if (!user) return unauthorized();
  const { id } = await params;
  const owned = await db.job.findUnique({
    where: { id },
    select: { userId: true, status: true },
  });
  if (!owned || owned.userId !== user.id) {
    return NextResponse.json({ error: "Job not found." }, { status: 404 });
  }
  if (owned.status === "running" || owned.status === "queued") {
    return NextResponse.json(
      { error: "Cannot delete a running job." },
      { status: 409 },
    );
  }
  try {
    await engine.deleteJob(id);
  } catch {
    // engine may have pruned it already; still remove locally
  }
  await db.job.delete({ where: { id } });
  return NextResponse.json({ deleted: id });
}
