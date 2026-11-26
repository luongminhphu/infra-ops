import type { FrontendModule } from "@/core/modules";
import { lifecycleModule } from "./lifecycle";
import { workloadModule } from "./workload";
import { handbookModule } from "./handbook";

export const modules: FrontendModule[] = [lifecycleModule, workloadModule, handbookModule];
