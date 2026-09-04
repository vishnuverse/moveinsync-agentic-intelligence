// highcharts-react-official ships a UMD bundle whose CJS `module.exports` is
// itself `{ HighchartsReact, default: HighchartsReact }` -- under Vite's
// esbuild CJS->ESM interop that whole object becomes the *ESM* default
// export, so `import HighchartsReact from "highcharts-react-official"`
// resolves to an object, not the component, and React throws "Element type
// is invalid". Unwrapped once here so every chart wrapper can import the
// real component regardless of how a given bundler's interop happens to
// nest it.
import type { ComponentType } from "react";
import * as HighchartsReactModule from "highcharts-react-official";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const mod = HighchartsReactModule as any;

// A valid React component reference is either a plain function or a
// React.memo/forwardRef object carrying a react.* $$typeof symbol -- `typeof`
// alone says "object" for the latter, which is exactly what
// highcharts-react-official exports (it wraps the component in React.memo),
// so a function-only check wrongly rejects it.
function isComponentLike(x: unknown): x is ComponentType<Record<string, unknown>> {
  return typeof x === "function" || (typeof x === "object" && x !== null && "$$typeof" in x);
}

const found = [mod?.HighchartsReact, mod?.default?.HighchartsReact, mod?.default?.default, mod?.default, mod].find(
  isComponentLike,
);

if (!found) {
  throw new Error("HighchartsReactCompat: could not resolve the HighchartsReact component export");
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export default found as ComponentType<any>;
