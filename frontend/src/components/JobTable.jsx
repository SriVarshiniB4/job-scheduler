import StatusPill from "./StatusPill";

export default function JobTable({ jobs }) {
  return (
    <div className="panel">
      <p className="panel-title">Live jobs</p>
      <table className="job-table">
        <thead>
          <tr>
            <th>Job</th>
            <th>Type</th>
            <th>Status</th>
            <th>Attempt</th>
            <th>Worker</th>
          </tr>
        </thead>
        <tbody>
          {jobs.length === 0 && (
            <tr>
              <td colSpan={5} style={{ color: "var(--text-faint)" }}>
                Waiting for the first job event…
              </td>
            </tr>
          )}
          {jobs.map((j) => (
            <tr key={j.job_id}>
              <td>{j.job_id.slice(0, 8)}</td>
              <td>{j.job_type}</td>
              <td><StatusPill status={j.status} /></td>
              <td>{j.attempts}</td>
              <td>{j.worker_id ? j.worker_id.slice(0, 8) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
