/**
 * The signature interaction of this whole project: click "kill" on a
 * worker and watch its card flatline, then watch a DIFFERENT worker
 * pick up whatever it was holding once the lease expires. This isn't
 * simulated in the UI — the button hits a real (dev-only) backend
 * endpoint that sends SIGKILL to that worker's OS process. What you're
 * watching is the actual crash-recovery mechanism, live.
 */
export default function WorkerFleet({ workers }) {
  const ids = Object.keys(workers);

  async function handleKill(workerId) {
    await fetch(`/api/dev/kill-worker/${workerId}`, { method: "POST" }).catch(() => {});
  }

  if (ids.length === 0) {
    return (
      <div className="panel" style={{ marginBottom: 20 }}>
        <p className="panel-title">Worker fleet</p>
        <p style={{ color: "var(--text-faint)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
          No workers have reported in yet — start one to see it appear here.
        </p>
      </div>
    );
  }

  return (
    <div className="panel" style={{ marginBottom: 20 }}>
      <p className="panel-title">Worker fleet</p>
      <div className="worker-fleet">
        {ids.map((id) => {
          const w = workers[id];
          return (
            <div key={id} className={`worker-card ${w.dead ? "dead" : ""}`}>
              {w.dead && <span className="reclaim-badge">lease expired</span>}
              <div className="label">
                <span>{id.slice(0, 8)}</span>
                <span style={{ color: w.dead ? "var(--status-dead)" : "var(--status-completed)" }}>
                  {w.dead ? "dead" : "alive"}
                </span>
              </div>
              <Heartbeat alive={!w.dead} />
              <div className="current-job">
                {w.currentJob ? `job ${w.currentJob.slice(0, 8)}…` : "idle"}
              </div>
              {!w.dead && (
                <button className="kill-btn" onClick={() => handleKill(id)}>
                  kill -9
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Heartbeat({ alive }) {
  // A simple animated line: jagged pulse while alive, flat once dead.
  const path = alive
    ? "M0,10 L10,10 L14,3 L18,17 L22,10 L32,10 L36,4 L40,16 L44,10 L60,10"
    : "M0,10 L60,10";
  return (
    <svg className="heartbeat-line" viewBox="0 0 60 20" width="100%" preserveAspectRatio="none">
      <path
        d={path}
        fill="none"
        stroke={alive ? "var(--status-running)" : "var(--status-dead)"}
        strokeWidth="1.5"
      />
    </svg>
  );
}
