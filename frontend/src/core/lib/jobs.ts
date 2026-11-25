/** A job in one of these states will not change on its own any more (stop polling it). */
export const isSettled = (status: string | undefined) => status === "completed" || status === "failed" || status === "cancelled";
