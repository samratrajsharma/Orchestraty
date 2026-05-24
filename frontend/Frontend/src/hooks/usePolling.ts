import { useState, useEffect, useRef } from "react";
import { taskApi } from "../api/tasks";

interface TaskStatus {
  id: string;
  status: string;
  progress: number;
  progress_message?: string;
  result?: unknown;
  error?: string;
}

export function useTaskPolling(taskId: string | null, interval = 2000) {
  const [task, setTask] = useState<TaskStatus | null>(null);
  const [polling, setPolling] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!taskId) return;
    setPolling(true);

    const poll = async () => {
      try {
        const res = await taskApi.get(taskId);
        setTask(res.data);
        if (res.data.status === "completed" || res.data.status === "failed") {
          setPolling(false);
          if (timerRef.current) clearInterval(timerRef.current);
        }
      } catch { setPolling(false); }
    };

    poll();
    timerRef.current = setInterval(poll, interval);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [taskId, interval]);

  return { task, polling };
}
