import { useCallback, useEffect, useState } from "react";
import { withTimeout } from "../lib/timeout";

export type ChartFetchStatus = "loading" | "ready" | "error";

// Each persona Dashboard fires 2-3 of these independently of the metric
// grid's own load -- previously with no .catch() at all, so one hung/failed
// chart request just left that panel blank forever with no way to retry.
export function useChartFetch<T>(fetcher: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [status, setStatus] = useState<ChartFetchStatus>("loading");

  const load = useCallback(() => {
    setStatus("loading");
    withTimeout(fetcher())
      .then((res) => {
        setData(res);
        setStatus("ready");
      })
      .catch(() => setStatus("error"));
    // fetcher is a stable api.* reference passed in at the call site
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return { data, status, reload: load };
}
