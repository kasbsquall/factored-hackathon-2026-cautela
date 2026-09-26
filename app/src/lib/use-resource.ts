"use client";

import { useCallback, useEffect, useState } from "react";

export type Resource<T> =
  | { status: "loading" }
  | { status: "error"; error: unknown }
  | { status: "ready"; data: T };

/** Loads once per key; a newer key or unmount discards the older response. */
export function useResource<T>(key: string, load: () => Promise<T>): Resource<T> & { reload: () => void } {
  const [state, setState] = useState<Resource<T>>({ status: "loading" });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let current = true;
    setState({ status: "loading" });
    load().then(
      (data) => current && setState({ status: "ready", data }),
      (error: unknown) => current && setState({ status: "error", error }),
    );
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { ...state, reload };
}
