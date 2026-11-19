"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ErrorBar,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Award, Scale, TrendingUp } from "lucide-react";
import {
  LeaderboardEntry,
  PairedStat,
  SYSTEM_COLORS,
  fmtNum,
  fmtPct,
} from "@/lib/dashboard-constants";

export function Leaderboard({
  entries,
  totalEpisodes,
  latestStats,
  refreshKey,
}: {
  entries: LeaderboardEntry[];
  totalEpisodes: number;
  latestStats: PairedStat[];
  refreshKey: number;
}) {
  const [windowW, setWindowW] = useState(900);
  useEffect(() => {
    const onR = () => setWindowW(window.innerWidth);
    onR();
    window.addEventListener("resize", onR);
    return () => window.removeEventListener("resize", onR);
  }, []);

  const chartData = entries.map((e) => ({
    system: e.system,
    rate: Number((e.successRate * 100).toFixed(1)),
    ci: [Number(((e.successRate - e.ciLo) * 100).toFixed(1)), Number(((e.ciHi - e.successRate) * 100).toFixed(1))],
    fill: SYSTEM_COLORS[e.system] ?? "#64748b",
  }));

  const best = entries[0];

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm">
              <Award className="h-4 w-4 text-emerald-600" /> Leader
            </CardTitle>
          </CardHeader>
          <CardContent>
            {best ? (
              <>
                <div
                  className="text-2xl font-bold"
                  style={{ color: SYSTEM_COLORS[best.system] }}
                >
                  {best.system}
                </div>
                <p className="text-sm text-muted-foreground">
                  {fmtPct(best.successRate)} success over {best.n} episodes ·
                  CI [{fmtPct(best.ciLo)}, {fmtPct(best.ciHi)}]
                </p>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">
                No episodes recorded yet.
              </p>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Total episodes (yours)</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{totalEpisodes}</div>
            <p className="text-sm text-muted-foreground">
              across {entries.length} systems · persists across sessions
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm">
              <Scale className="h-4 w-4 text-emerald-600" /> RG vs baselines
            </CardTitle>
          </CardHeader>
          <CardContent>
            {latestStats.length > 0 ? (
              <p className="text-sm text-muted-foreground">
                Latest batch:{" "}
                {latestStats
                  .map((s) => `${s.vs} ${s.delta_success_mean > 0 ? "+" : ""}${fmtNum(s.delta_success_mean, 2)}`)
                  .join(", ")}
              </p>
            ) : (
              <p className="text-sm text-muted-foreground">
                Run a batch with RG + another system to see paired deltas.
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <TrendingUp className="h-4 w-4 text-emerald-600" /> Success rate by
            system
          </CardTitle>
          <CardDescription>
            Wilson 95% score intervals; all conditions pooled, your episode
            database.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {entries.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              Submit a batch in the Control Room to populate the leaderboard.
            </p>
          ) : (
            <div style={{ width: "100%", height: 300 }}>
              <ResponsiveContainer>
                <BarChart
                  data={chartData}
                  margin={{ top: 12, right: 16, bottom: 8, left: 0 }}
                >
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e4e4e7" />
                  <XAxis
                    dataKey="system"
                    tick={{ fontSize: 12 }}
                    interval={0}
                  />
                  <YAxis
                    domain={[0, 100]}
                    tick={{ fontSize: 12 }}
                    tickFormatter={(v: number) => `${v}%`}
                    width={44}
                  />
                  <ChartTooltip
                    formatter={(value: unknown, name: unknown) => {
                      const v = Number(value);
                      return name === "rate"
                        ? [`${Number.isFinite(v) ? v.toFixed(1) : value}%`, "success"]
                        : [value as React.ReactNode, String(name)];
                    }}
                  />
                  <Bar dataKey="rate" radius={[4, 4, 0, 0]} maxBarSize={56}>
                    {chartData.map((d) => (
                      <Cell key={d.system} fill={d.fill} />
                    ))}
                    <ErrorBar dataKey="ci" width={4} strokeWidth={1.5} stroke="#3f3f46" direction="y" />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">System scoreboard</CardTitle>
          <CardDescription>
            Pooled aggregates; diagnosis accuracy excludes stale-dependency by
            design.
          </CardDescription>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>System</TableHead>
                <TableHead>n</TableHead>
                <TableHead>Success</TableHead>
                <TableHead>95% CI</TableHead>
                <TableHead>Extra actions</TableHead>
                <TableHead>Recovery t (s)</TableHead>
                <TableHead>Diag acc</TableHead>
                <TableHead>Escal.</TableHead>
                <TableHead>False-alarm ep.</TableHead>
                <TableHead>Unsafe</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map((e) => (
                <TableRow key={e.system}>
                  <TableCell>
                    <span className="flex items-center gap-2">
                      <span
                        className="inline-block h-2.5 w-2.5 rounded-full"
                        style={{ background: SYSTEM_COLORS[e.system] }}
                      />
                      <span className="font-medium">{e.system}</span>
                    </span>
                  </TableCell>
                  <TableCell>{e.n}</TableCell>
                  <TableCell className="font-medium">
                    {fmtPct(e.successRate)}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    [{fmtPct(e.ciLo, 0)}, {fmtPct(e.ciHi, 0)}]
                  </TableCell>
                  <TableCell>{fmtNum(e.extraActionsMean)}</TableCell>
                  <TableCell>{fmtNum(e.recoveryTimeMean, 2)}</TableCell>
                  <TableCell>
                    {e.diagAccuracy === null ? (
                      "—"
                    ) : (
                      <Badge variant="secondary">{fmtPct(e.diagAccuracy, 0)}</Badge>
                    )}
                  </TableCell>
                  <TableCell>{e.escalations}</TableCell>
                  <TableCell>{fmtPct(e.falseAlarmEpisodeRate, 1)}</TableCell>
                  <TableCell className={e.unsafeExecutions > 0 ? "font-medium text-destructive" : ""}>
                    {e.unsafeExecutions}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
