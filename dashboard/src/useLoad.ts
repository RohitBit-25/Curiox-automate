import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

/** Fetch on mount and whenever deps change; `reload()` refetches; `poll` (ms) refetches on an interval. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[] = [], poll?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [loading, setLoading] = useState(true);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const load = useCallback(fn, deps);
  const reload = useCallback(() => {
    setLoading(true);
    return load().then(
      (d) => { setData(d); setError(null); },
      (e: Error) => setError(e),
    ).finally(() => setLoading(false));
  }, [load]);
  useEffect(() => { reload(); }, [reload]);
  useEffect(() => {
    if (!poll) return;
    const t = setInterval(reload, poll);
    return () => clearInterval(t);
  }, [poll, reload]);
  return { data, error, loading, reload, setData };
}

/** Run an action, surface its error, then call onDone. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);
  const run = async <R,>(fn: () => Promise<R>, onDone?: (r: R) => void): Promise<R | undefined> => {
    setBusy(true);
    setError("");
    try {
      const r = await fn();
      onDone?.(r);
      return r;
    } catch (e) {
      if (mounted.current) setError((e as Error).message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  };
  return { busy, error, setError, run };
}

/** Debounced value. */
export function useDebounced<T>(value: T, ms = 400) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}
