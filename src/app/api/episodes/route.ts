import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { currentUser, unauthorized } from "@/lib/session";
import { rateLimit, RULES } from "@/lib/rate-limit";

// Episode browser: paginated per-user episode index from Prisma with filters.
export async function GET(req: Request) {
  const user = await currentUser(req);
  if (!user) return unauthorized();
  const rl = rateLimit(`read:${user.id}`, RULES.read);
  if (!rl.ok) {
    return NextResponse.json({ error: "Rate limit." }, { status: 429 });
  }
  const url = new URL(req.url);
  const system = url.searchParams.get("system") ?? undefined;
  const fault = url.searchParams.get("fault") ?? undefined;
  const outcome = url.searchParams.get("outcome") ?? undefined;
  const jobId = url.searchParams.get("jobId") ?? undefined;
  const page = Math.max(1, Math.floor(Number(url.searchParams.get("page") ?? 1)));
  const pageSize = Math.min(
    100,
    Math.max(5, Math.floor(Number(url.searchParams.get("pageSize") ?? 25))),
  );

  const where = {
    userId: user.id,
    ...(system && system !== "all" ? { system } : {}),
    ...(fault && fault !== "all" ? { fault } : {}),
    ...(outcome && outcome !== "all" ? { outcome } : {}),
    ...(jobId ? { jobId } : {}),
  };
  const [total, rows] = await Promise.all([
    db.episodeRow.count({ where }),
    db.episodeRow.findMany({
      where,
      orderBy: { createdAt: "desc" },
      skip: (page - 1) * pageSize,
      take: pageSize,
      include: { job: { select: { label: true } } },
    }),
  ]);
  return NextResponse.json({
    total,
    page,
    pageSize,
    rows: rows.map((r) => ({
      id: r.id,
      jobId: r.jobId,
      jobLabel: r.job.label,
      epId: r.epId,
      system: r.system,
      condition: r.condition,
      horizon: r.horizon,
      seed: r.seed,
      fault: r.fault,
      outcome: r.outcome,
      success: r.success,
      extraActions: r.extraActions,
      recoveryTimeS: r.recoveryTimeS,
      diagCorrect: r.diagCorrect,
      escalations: r.escalations,
      interventions: r.interventions,
    })),
  });
}
