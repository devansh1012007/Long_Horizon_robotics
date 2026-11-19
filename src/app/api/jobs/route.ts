import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { currentUser, serverError, unauthorized } from "@/lib/session";
import { EngineError, engine, syncJobToDb, type JobSpec } from "@/lib/engine-client";
import { rateLimit, RULES } from "@/lib/rate-limit";

const VALID_HORIZONS = [1, 2, 3, 5, 8];
const VALID_CONDITIONS = ["none", "single", "compound"];
const MAX_ACTIVE = Number(process.env.MAX_ACTIVE_JOBS_PER_USER ?? 2);

export async function GET(req: Request) {
  const user = await currentUser(req);
  if (!user) return unauthorized();
  const rl = rateLimit(`read:${user.id}`, RULES.read);
  if (!rl.ok) {
    return NextResponse.json({ error: "Rate limit." }, { status: 429 });
  }
  const jobs = await db.job.findMany({
    where: { userId: user.id },
    orderBy: { createdAt: "desc" },
    take: 30,
  });

  // Live-sync with the engine: refresh queued/running jobs, capture fresh
  // summaries for finished ones, and detect jobs the engine lost (restart).
  try {
    const { jobs: engineJobs } = await engine.listJobs(user.id);
    const byId = new Map(engineJobs.map((j) => [j.id, j]));
    for (const j of jobs) {
      const ej = byId.get(j.id);
      if (ej) {
        if (ej.status === "done" && j.status !== "done" && j.progress !== ej.progress) {
          // capture rows once, with summary + paired stats
          try {
            const full = await engine.getResult(j.id);
            await syncJobToDb(full);
            j.status = full.status;
            j.progress = full.progress;
            j.summary = JSON.stringify(full.summary);
            j.stats = JSON.stringify(full.stats);
            j.finishedAt = full.finished ? new Date(full.finished * 1000) : null;
          } catch {
            await syncJobToDb(ej);
            j.status = ej.status;
            j.progress = ej.progress;
          }
        } else if (j.status !== ej.status || j.progress !== ej.progress) {
          await syncJobToDb(ej);
          j.status = ej.status;
          j.progress = ej.progress;
          j.total = ej.total;
          if (ej.summary) j.summary = JSON.stringify(ej.summary);
          if (ej.stats) j.stats = JSON.stringify(ej.stats);
          if (ej.error) j.error = ej.error.slice(0, 2000);
        }
      } else if (j.status === "queued" || j.status === "running") {
        await db.job.update({
          where: { id: j.id },
          data: { status: "failed", error: "Lost by the engine (restart or prune)." },
        });
        j.status = "failed";
        j.error = "Lost by the engine (restart or prune).";
      }
    }
  } catch {
    // engine unreachable: serve stored state as-is
  }

  return NextResponse.json({
    jobs: jobs.map((j) => ({
      id: j.id,
      label: j.label,
      spec: JSON.parse(j.spec || "{}"),
      status: j.status,
      progress: j.progress,
      total: j.total,
      summary: j.summary ? JSON.parse(j.summary) : null,
      stats: j.stats ? JSON.parse(j.stats) : null,
      error: j.error,
      createdAt: j.createdAt,
      finishedAt: j.finishedAt,
    })),
  });
}

export async function POST(req: Request) {
  const user = await currentUser(req);
  if (!user) return unauthorized();
  const rl = rateLimit(`submit:${user.id}`, RULES.submit);
  if (!rl.ok) {
    return NextResponse.json(
      { error: "Slow down — too many submissions." },
      { status: 429, headers: { "Retry-After": String(rl.retryAfterS) } },
    );
  }
  const body = await req.json().catch(() => null);
  if (!body) return serverError("Invalid JSON body.", 400);

  const systems: string[] = Array.isArray(body.systems) ? body.systems : [];
  const horizons: number[] = Array.isArray(body.horizons)
    ? body.horizons.map(Number)
    : [];
  const condition: string = String(body.condition ?? "single");
  const fault: string | null = body.fault ? String(body.fault) : null;
  const episodes = Math.floor(Number(body.episodes ?? 15));
  const base_seed = Math.floor(Number(body.base_seed ?? 4200));
  const label = String(body.label ?? "").slice(0, 120);

  if (systems.length < 1 || systems.length > 12) {
    return serverError("Pick between 1 and 12 systems.", 400);
  }
  if (horizons.length < 1 || horizons.some((h) => !VALID_HORIZONS.includes(h))) {
    return serverError(`Horizons must be chosen from ${VALID_HORIZONS}.`, 400);
  }
  if (!VALID_CONDITIONS.includes(condition)) {
    return serverError("Condition must be none, single or compound.", 400);
  }
  if (!Number.isFinite(episodes) || episodes < 1 || episodes > 100) {
    return serverError("Episodes per cell must be 1-100.", 400);
  }
  const total = systems.length * horizons.length * episodes;
  if (total > 600) {
    return serverError(
      `Job too large: ${total} episodes (max 600). Reduce systems, horizons or episodes.`,
      400,
    );
  }

  // Per-user active job quota: ask the engine how many are queued/running.
  try {
    const { jobs } = await engine.listJobs(user.id);
    const active = jobs.filter((j) => j.status === "queued" || j.status === "running");
    if (active.length >= MAX_ACTIVE) {
      return NextResponse.json(
        {
          error: `You already have ${MAX_ACTIVE} job${MAX_ACTIVE === 1 ? "" : "s"} queued or running. Wait for them to finish.`,
        },
        { status: 429 },
      );
    }
    const spec: JobSpec = {
      systems,
      horizons,
      condition,
      fault,
      episodes,
      base_seed,
      label,
    };
    const submitted = await engine.submit(user.id, spec);
    await db.job.create({
      data: {
        id: submitted.job_id,
        userId: user.id,
        label: label || `Batch ${new Date().toLocaleString()}`,
        spec: JSON.stringify(spec),
        status: "queued",
        total: submitted.total,
      },
    });
    return NextResponse.json(submitted);
  } catch (e) {
    if (e instanceof EngineError) {
      return serverError(e.message, e.status);
    }
    return serverError("Engine unavailable. Try again shortly.", 503);
  }
}
