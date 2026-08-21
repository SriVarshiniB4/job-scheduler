const COLORS = {
  queued: "var(--status-queued)",
  claimed: "var(--status-claimed)",
  running: "var(--status-running)",
  completed: "var(--status-completed)",
  failed: "var(--status-claimed)",
  dead_letter: "var(--status-dead)",
};

export default function StatusPill({ status }) {
  const color = COLORS[status] || "var(--text-muted)";
  return (
    <span className="status-pill" style={{ background: `${color}22`, color }}>
      <span className="status-dot" style={{ background: color }} />
      {status.replace("_", " ")}
    </span>
  );
}
