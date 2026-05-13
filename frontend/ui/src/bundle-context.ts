import { createContext, useContext } from "react";

/** Incrementing counter bumped whenever the user changes the active
 *  bundle. Views add this to their useEffect deps so they refetch on
 *  switch — no remount, so element references stay stable for assertions
 *  in flight. */
export const BundleVersionContext = createContext(0);

export function useBundleVersion(): number {
  return useContext(BundleVersionContext);
}
