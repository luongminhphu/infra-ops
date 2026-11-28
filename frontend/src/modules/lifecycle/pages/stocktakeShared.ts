export type Condition = "good" | "damaged" | "broken";

/** The one set of condition labels used by the walk and the review screens. */
export const CONDITION: Record<Condition, string> = { good: "Tốt", damaged: "Hư hỏng nhẹ", broken: "Hỏng, không dùng được" };
