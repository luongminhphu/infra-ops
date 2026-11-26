import type { FrontendModule } from "@/core/modules";
import { lifecycleModule } from "./lifecycle";
import { workloadModule } from "./workload";
import { playbookModule } from "./playbook";

export const modules: FrontendModule[] = [lifecycleModule, workloadModule, playbookModule];
