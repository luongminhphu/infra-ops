import { createContext, useContext, useLayoutEffect, type Dispatch, type SetStateAction } from "react";

/** Focus mode: the module tool sub-nav is hidden; `highlight` (a nav path) is the rail entry shown as active. */
export interface FocusState {
  highlight?: string;
}
export const FocusContext = createContext<Dispatch<SetStateAction<FocusState | null>> | null>(null);

/** A page calls this to hide the tool sub-nav while mounted (and optionally keep another rail entry lit). */
export function useFocusLayout({ highlight }: { highlight?: string } = {}) {
  const set = useContext(FocusContext);
  useLayoutEffect(() => {
    set?.({ highlight });
    return () => set?.(null);
  }, [set, highlight]);
}
