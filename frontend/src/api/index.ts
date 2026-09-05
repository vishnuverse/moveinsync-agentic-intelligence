import { realClient } from "./apiClient";
import { mockClient } from "./mockClient";
import type { ApiClient } from "./types";

// Default to the REAL backend; mock is opt-in via VITE_USE_MOCK="true".
// (The docker/demo build sets VITE_USE_MOCK=false explicitly, so this flip
// only changes standalone dev, which now talks to the real backend by default.)
const useMock = import.meta.env.VITE_USE_MOCK === "true";

export const api: ApiClient = useMock ? mockClient : realClient;

export * from "./types";
