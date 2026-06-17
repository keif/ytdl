import { useEffect, useRef, useState } from "react";

export interface JobEvent {
  event: string;
  job_id?: string;
  jobs?: { id: string; url: string; status: string; title: string | null }[];
  status?: string;
  downloaded_bytes?: number;
  total_bytes?: number;
  speed?: number;
  eta?: number;
  error?: string;
}

export function useJobsStream(onEvent: (e: JobEvent) => void): "connecting" | "open" | "closed" {
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;
  const [state, setState] = useState<"connecting" | "open" | "closed">("connecting");

  useEffect(() => {
    const es = new EventSource("/events");
    es.onopen = () => setState("open");
    es.onerror = () => setState("connecting");
    es.onmessage = (m) => {
      try {
        handlerRef.current(JSON.parse(m.data));
      } catch {
        /* ignore malformed lines */
      }
    };
    return () => {
      es.close();
      setState("closed");
    };
  }, []);

  return state;
}
