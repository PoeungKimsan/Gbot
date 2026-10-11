/**
 * The build contract: `web/dist`, relocatable assets, and a disclosure guard.
 *
 * `vite.config.ts` is imported rather than parsed, so these are assertions about the object
 * Vite actually sees. If someone changes `outDir` to something the FastAPI mount does not
 * know about, this fails rather than the deployment doing so.
 */

import { describe, expect, it } from "vitest";
import raw from "../../vite.config";

interface ConfigShape {
  base?: string;
  build?: { outDir?: string; emptyOutDir?: boolean; target?: string };
  server?: { proxy?: Record<string, { target?: string }> };
  test?: { environment?: string; setupFiles?: string[] };
  plugins?: unknown[];
}

/**
 * `defineConfig` accepts either a config object or a function of the mode. Vitest's own
 * typing collapses that union to the object form, so the function branch is checked at
 * runtime and narrowed through a cast rather than trusted to the compiler.
 */
const resolved: ConfigShape =
  typeof raw === "function"
    ? (raw as unknown as () => ConfigShape)()
    : (raw as unknown as ConfigShape);

describe("the build output", () => {
  it("goes to web/dist, which is what the API mounts", () => {
    expect(resolved.build?.outDir).toBe("dist");
  });

  it("is emptied before each build", () => {
    expect(resolved.build?.emptyOutDir).toBe(true);
  });

  it("targets a modern engine and not a legacy one", () => {
    expect(resolved.build?.target).toBe("es2022");
  });

  it("emits relocatable assets, because the API mounts this directory", () => {
    // Absolute paths would 404 the moment the mount prefix changed.
    expect(resolved.base).toBe("./");
  });
});

describe("the dev server", () => {
  it("proxies the relative /api endpoints at the API's own bind", () => {
    expect(resolved.server?.proxy?.["/api"]?.target).toBe("http://127.0.0.1:8080");
  });
});

describe("the disclosure guard", () => {
  it("is installed", () => {
    const names = (resolved.plugins ?? []).map((plugin) =>
      typeof plugin === "object" && plugin !== null && "name" in plugin
        ? String((plugin as { name: unknown }).name)
        : "",
    );
    expect(names).toContain("xauusd-disclosure-guard");
  });

  it("runs before anything else", () => {
    // `enforce: "pre"` is what makes it fail before `?raw` resolution can produce an
    // unresolved-import stack trace instead of a named error.
    const guard = (resolved.plugins ?? []).find(
      (plugin) =>
        typeof plugin === "object" &&
        plugin !== null &&
        (plugin as { name?: string }).name === "xauusd-disclosure-guard",
    ) as { enforce?: string } | undefined;
    expect(guard?.enforce).toBe("pre");
  });
});

describe("the test environment", () => {
  it("is jsdom, with a setup file that registers the DOM matchers", () => {
    expect(resolved.test?.environment).toBe("jsdom");
    expect(resolved.test?.setupFiles).toEqual(["./src/test/setup.ts"]);
  });
});
