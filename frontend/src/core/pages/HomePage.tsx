import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "@/core/auth/AuthProvider";
import { firstLanding, markLanded } from "@/core/auth/landing";
import { DashboardPage } from "./DashboardPage";

/** Home route: an Operator's first stop in a session is "Việc của tôi" (their queue); afterwards, and for every
 * other role, it is Tổng quan. */
export function HomePage() {
  const { me } = useAuth();
  const roles = me?.roles ?? [];
  const operatorOnly = roles.includes("operator") && !roles.some((r) => ["supervisor", "manager", "admin"].includes(r));
  // Read once per mount (StrictMode renders twice); mark the session only once the user is known.
  const [first] = useState(firstLanding);
  useEffect(() => {
    if (me) markLanded();
  }, [me]);
  return operatorOnly && first ? <Navigate to="/me/work" replace /> : <DashboardPage />;
}
