/** Session flag: has this tab already landed on its home page since the last sign-in? */
const LANDED = "infraops.landed";

export function firstLanding(): boolean {
  try {
    return !sessionStorage.getItem(LANDED);
  } catch {
    return false; // storage blocked: no redirect
  }
}

export function markLanded(): void {
  try {
    sessionStorage.setItem(LANDED, "1");
  } catch {
    // storage blocked
  }
}

/** Called on the sign-in page (after a sign-out or an expired session) so the next sign-in lands on the role's home. */
export function resetLanding(): void {
  try {
    sessionStorage.removeItem(LANDED);
  } catch {
    // storage blocked
  }
}
