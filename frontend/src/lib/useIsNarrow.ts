import * as React from "react";

/** True when the viewport is narrower than 768px; listens to changes. */
export function useIsNarrow(): boolean {
  const get = React.useCallback((): boolean => {
    try {
      return window.matchMedia("(max-width: 767px)").matches;
    } catch {
      return false;
    }
  }, []);
  const [narrow, setNarrow] = React.useState<boolean>(get);
  React.useEffect(() => {
    let mq: MediaQueryList | null = null;
    try {
      mq = window.matchMedia("(max-width: 767px)");
    } catch {
      return;
    }
    const onChange = () => setNarrow(mq?.matches ?? false);
    onChange();
    if (typeof mq.addEventListener === "function") {
      mq.addEventListener("change", onChange);
      return () => mq?.removeEventListener("change", onChange);
    }
    // Fallback for older browsers.
    const legacy = mq as MediaQueryList & {
      addListener?: (fn: () => void) => void;
      removeListener?: (fn: () => void) => void;
    };
    legacy.addListener?.(onChange);
    return () => legacy.removeListener?.(onChange);
  }, []);
  return narrow;
}
