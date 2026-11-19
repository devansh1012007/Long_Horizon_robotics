// Engine sidecar supervisor: owns the Python engine-service process as a
// child of the Next.js server (survives across requests, auto-restarts).
//
// - Health-check first: if an engine already listens on 3031 (e.g. left over
//   from a previous module instance or hot reload), reuse it.
// - Otherwise spawn `python index.py` and wait for /health.
// - The mutex lives on globalThis so hot reload never double-spawns.
import { spawn } from "child_process";
import { appendFileSync, mkdirSync, openSync } from "fs";
import path from "path";

// Engine location & spawn policy:
//  - ENGINE_URL unset or loopback -> sidecar mode: spawn the python engine
//    from ENGINE_DIR and wait for /health.
//  - ENGINE_URL remote (container/cluster) -> never spawn; health-check only.
const ENGINE_URL = (process.env.ENGINE_URL ?? "http://127.0.0.1:3031").replace(
  /\/$/,
  "",
);
const ENGINE_DIR =
  process.env.ENGINE_DIR ?? path.join(process.cwd(), "engine");
const PYTHON_BIN = process.env.PYTHON_BIN ?? "python3";
const LOG_DIR =
  process.env.ENGINE_LOG_DIR ?? path.join(process.cwd(), ".zscripts");
const LOG_FILE = path.join(LOG_DIR, "engine-service.log");

const isLoopback = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/i.test(
  ENGINE_URL,
);

type SupervisorState = {
  starting?: Promise<boolean>;
  pid?: number;
  lastSpawn?: number;
};

const g = globalThis as unknown as { __rgEngineSupervisor?: SupervisorState };
const state: SupervisorState = (g.__rgEngineSupervisor ??= {});

async function fetchTimeout(url: string, ms: number): Promise<Response | null> {
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), ms);
    const res = await fetch(url, { signal: ctl.signal });
    clearTimeout(t);
    return res;
  } catch {
    return null;
  }
}

export async function engineHealthy(timeoutMs = 1200): Promise<boolean> {
  const res = await fetchTimeout(`${ENGINE_URL}/health`, timeoutMs);
  return !!res && res.ok;
}

async function spawnEngine(): Promise<boolean> {
  if (await engineHealthy()) return true;
  try {
    mkdirSync(LOG_DIR, { recursive: true });
    const logFd = openSync(LOG_FILE, "a");
    appendFileSync(
      LOG_FILE,
      `\n[gateway] spawning engine sidecar at ${new Date().toISOString()}\n`,
    );
    const child = spawn(PYTHON_BIN, ["index.py"], {
      cwd: ENGINE_DIR,
      stdio: ["ignore", logFd, logFd],
      env: {
        ...process.env,
        PYTHONUNBUFFERED: "1",
        ENGINE_HOST: "127.0.0.1",
        ENGINE_PORT: new URL(ENGINE_URL).port || "3031",
      },
    });
    child.on("error", (err) => {
      appendFileSync(
        LOG_FILE,
        `[gateway] engine spawn error: ${String(err)}\n`,
      );
      state.starting = undefined;
    });
    child.on("exit", () => {
      if (state.pid === child.pid) state.pid = undefined;
    });
    state.pid = child.pid;
    state.lastSpawn = Date.now();
    // Wait for startup (numpy + engine imports take a few seconds).
    const deadline = Date.now() + 25_000;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 600));
      if (await engineHealthy(800)) return true;
    }
    return false;
  } catch {
    return false;
  }
}

/** Ensure the engine is reachable; returns true when healthy.
 *  Remote engines are never spawned — we only probe them. */
export async function ensureEngine(): Promise<boolean> {
  if (await engineHealthy()) return true;
  if (!isLoopback) return false;
  if (!state.starting) {
    state.starting = spawnEngine().finally(() => {
      state.starting = undefined;
    });
  }
  return state.starting;
}

export function engineStatus() {
  return {
    pid: state.pid ?? null,
    port: Number(new URL(ENGINE_URL).port || 3031),
    url: ENGINE_URL,
    mode: isLoopback ? "sidecar" : "remote",
  };
}
