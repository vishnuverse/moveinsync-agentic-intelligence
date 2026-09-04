import { realClient } from "./apiClient";
import { mockClient } from "./mockClient";
import type { ApiClient } from "./types";

const useMock = import.meta.env.VITE_USE_MOCK !== "false";

export const api: ApiClient = useMock ? mockClient : realClient;

export * from "./types";
