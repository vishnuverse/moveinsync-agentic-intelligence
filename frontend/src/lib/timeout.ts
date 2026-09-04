// Shared client-side timeout for data fetches. The API layer's own promise
// never resolves/rejects on its own if a request hangs (confirmed live:
// /api/dashboard can hang 650+ seconds before the proxy 502s) -- this wraps
// any api.* call so the UI can stop waiting and offer a retry instead of
// staying on a loading state forever.
export class TimeoutError extends Error {
  constructor(ms: number) {
    super(`Timed out after ${ms}ms`);
    this.name = "TimeoutError";
  }
}

export function withTimeout<T>(promise: Promise<T>, ms = 20000): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new TimeoutError(ms)), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (err) => {
        clearTimeout(timer);
        reject(err);
      },
    );
  });
}
