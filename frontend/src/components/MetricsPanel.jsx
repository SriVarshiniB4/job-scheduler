import { LineChart, Line, ResponsiveContainer, YAxis } from "recharts";
import { useEffect, useRef, useState } from "react";

export default function MetricsPanel({ completedCount, throughputPerMin, jobs, eventLog }) {
  const [history, setHistory] = useState([]);
  const lastCount = useRef(completedCount);

  useEffect(() => {
    const interval = setInterval(() => {
      setHistory((prev) => [...prev.slice(-29), { t: Date.now(), v: throughputPerMin }]);
    }, 2000);
    return () => clearInterval(interval);
  }, [throughputPerMin]);

  const statusCounts = jobs.reduce((acc, j) => {
    acc[j.status] = (acc[j.status] || 0) + 1;
    return acc;
  }, {});

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div className="panel">
        <p className="panel-title">Throughput</p>
        <div className="metric-row">
          <span className="metric-label">completions / min</span>
          <span className="metric-value">{throughputPerMin}</span>
        </div>
        <div className="metric-row">
          <span className="metric-label">total completed (session)</span>
          <span className="metric-value">{completedCount}</span>
        </div>
        <div style={{ height: 50, marginTop: 10 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={history}>
              <YAxis hide domain={[0, "dataMax + 2"]} />
              <Line
                type="monotone"
                dataKey="v"
                stroke="var(--status-running)"
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="panel">
        <p className="panel-title">Status breakdown (visible feed)</p>
        {Object.entries(statusCounts).length === 0 && (
          <p style={{ color: "var(--text-faint)", fontSize: 12, fontFamily: "var(--font-mono)" }}>
            No jobs yet
          </p>
        )}
        {Object.entries(statusCounts).map(([status, count]) => (
          <div className="metric-row" key={status}>
            <span className="metric-label">{status}</span>
            <span className="metric-value" style={{ fontSize: 13 }}>{count}</span>
          </div>
        ))}
      </div>

      <div className="panel">
        <p className="panel-title">Raw event log</p>
        <div className="event-log">
          {eventLog.length === 0 && <div>listening…</div>}
          {eventLog.map((e, i) => (
            <div className="ev-line" key={i}>
              {new Date(e.at).toLocaleTimeString()} <span className="job-type">{e.job_type}</span> → {e.status}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
