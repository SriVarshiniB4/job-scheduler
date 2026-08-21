import { useEffect, useRef, useState } from "react";

const MAX_JOB_ROWS = 40;
const MAX_LOG_LINES = 60;

/**
 * Owns the live connection to the backend's SSE stream and turns raw
 * job-status-change events into UI-ready state: a rolling job feed, a
 * worker map (derived from which worker_id last touched each job), and
 * a small set of running metrics.
 *
 * Deliberately does NOT poll anything — every update here is pushed by
 * the backend the instant Postgres's trigger fires. If this looks idle,
 * that's correct: it means nothing has changed, not that it's stuck.
 */
export function useJobEvents() {
  const [connected, setConnected] = useState(false);
  const [jobs, setJobs] = useState([]); // most recent first
  const [workers, setWorkers] = useState({}); // worker_id -> { lastSeen, currentJob, dead }
  const [eventLog, setEventLog] = useState([]);
  const [completedCount, setCompletedCount] = useState(0);
  const throughputWindowRef = useRef([]); // timestamps of recent completions

  useEffect(() => {
    const source = new EventSource("/events");

    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);

    source.onmessage = (msg) => {
      let evt;
      try {
        evt = JSON.parse(msg.data);
      } catch {
        return; // keep-alive/comment lines never reach onmessage; this is just defensive
      }

      setJobs((prev) => {
        const withoutThis = prev.filter((j) => j.job_id !== evt.job_id);
        return [evt, ...withoutThis].slice(0, MAX_JOB_ROWS);
      });

      setEventLog((prev) => [evt, ...prev].slice(0, MAX_LOG_LINES));

      if (evt.worker_id) {
        setWorkers((prev) => ({
          ...prev,
          [evt.worker_id]: {
            lastSeen: Date.now(),
            currentJob: evt.status === "completed" || evt.status === "failed" ? null : evt.job_id,
            dead: false,
          },
        }));
      }

      if (evt.status === "completed") {
        setCompletedCount((c) => c + 1);
        throughputWindowRef.current.push(Date.now());
      }
    };

    return () => source.close();
  }, []);

  // Mark workers dead if we haven't heard from them in a while (their
  // heartbeat renewals stop reaching us once the process is killed —
  // this is a UI-side inference layered on top of the real backend
  // reclaim logic, not a substitute for it).
  useEffect(() => {
    const interval = setInterval(() => {
      setWorkers((prev) => {
        const now = Date.now();
        const next = { ...prev };
        for (const id of Object.keys(next)) {
          if (!next[id].dead && now - next[id].lastSeen > 20000) {
            next[id] = { ...next[id], dead: true };
          }
        }
        return next;
      });
      // prune throughput window to last 60s
      throughputWindowRef.current = throughputWindowRef.current.filter(
        (t) => Date.now() - t < 60000
      );
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  const throughputPerMin = throughputWindowRef.current.length;

  return { connected, jobs, workers, eventLog, completedCount, throughputPerMin };
}

export async function fetchJobList() {
  const res = await fetch("/api/jobs?limit=40");
  if (!res.ok) return [];
  return res.json();
}
