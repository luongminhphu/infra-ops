import type { FrontendModule } from "@/core/modules";
import { lifecycleModule } from "./lifecycle";
import { workloadModule } from "./workload";

export const modules: FrontendModule[] = [lifecycleModule, workloadModule];
