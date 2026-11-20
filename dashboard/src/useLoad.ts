import { useCallback, useEffect, useState } from "react";

/** Fetch on mount and whenever deps change; `reload()` refetches. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const load = useCallback(fn, deps);
  const reload = useCallback(() => {
    setError("");
    load().then(setData, (e: Error) => setError(e.message));
  }, [load]);
  useEffect(reload, [reload]);
  return { data, error, reload };
}

/** Run an action, surface its error, then call onDone. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async (fn: () => Promise<unknown>, onDone?: () => void) => {
    setBusy(true);
    setError("");
    try {
      await fn();
      onDone?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run };
}
