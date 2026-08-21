import { useJobEvents } from "./useJobEvents";
import WorkerFleet from "./components/WorkerFleet";
import JobTable from "./components/JobTable";
import MetricsPanel from "./components/MetricsPanel";

export default function App() {
  const { connected, jobs, workers, eventLog, completedCount, throughputPerMin } = useJobEvents();

  return (
    <div className="app">
      <div className="header">
        <div>
          <h1>Job Scheduler</h1>
          <div className="subtitle">postgres-backed queue · lease-based fault tolerance</div>
        </div>
        <div className="conn-status">
          <span className={`conn-dot ${connected ? "live" : ""}`} />
          {connected ? "live" : "disconnected"}
        </div>
      </div>

      <WorkerFleet workers={workers} />

      <div className="grid">
        <JobTable jobs={jobs} />
        <MetricsPanel
          completedCount={completedCount}
          throughputPerMin={throughputPerMin}
          jobs={jobs}
          eventLog={eventLog}
        />
      </div>
    </div>
  );
}
