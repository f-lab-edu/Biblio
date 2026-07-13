import type { Api } from "./types";
import { createMockApi } from "./mock";
import { createHttpApi } from "./http";

export interface ApiConfig {
  useMock: boolean;
  baseUrl: string;
}

export function resolveApi(config: ApiConfig): Api {
  return config.useMock ? createMockApi() : createHttpApi(config.baseUrl);
}

export const api: Api = resolveApi({
  useMock: process.env.NEXT_PUBLIC_USE_MOCK !== "false",
  baseUrl: process.env.NEXT_PUBLIC_API_BASE_URL ?? "",
});

export type { Api } from "./types";
