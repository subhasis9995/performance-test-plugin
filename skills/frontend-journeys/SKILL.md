---
name: frontend-journeys
description: Create or update deterministic Playwright journeys (.perf/journeys/*.spec.ts) for frontend performance (Home, Login, Search, Product details, Add to cart, Checkout), optionally exploring the live app with Playwright MCP first. Use for "add a checkout journey", "measure web vitals for login", "create a frontend perf test".
argument-hint: "<journey name>"
allowed-tools: AskUserQuestion, Read, Write, Edit, Glob, mcp__plugin_perf_perf-engine__set_project_inputs
---

# Frontend journeys

Routine performance runs must never need an LLM driving the browser. Explore once, then **freeze** the journey as code.

1. Ask (AskUserQuestion) for the journey steps, and for any credentials as **env var names** (values go in `.env`).
2. Optionally explore the live app to find stable selectors with Playwright MCP, if the user has it (`claude mcp add playwright -- npx @playwright/mcp@0.0.85`). Do not explore production.
3. Write `.perf/journeys/<name>.spec.ts` in this shape:
```ts
import { test, type Page } from '@playwright/test';
export const lighthouseUrl = '/';            // optional: page audited by Lighthouse
type Ctx = { baseURL: string; env: Record<string, string | undefined>; mark: (n: string) => Promise<void> };
export async function journey(page: Page, ctx: Ctx) {
  await page.goto('/');
  await ctx.mark('home');
  // deterministic steps only: role/label/test-id selectors, explicit waits for visible results
}
if (!(globalThis as any).__PERF_RUNNER__) test('<Name>', async ({ page, baseURL }) => {
  await journey(page, { baseURL: baseURL!, env: process.env, mark: async () => {} });
});
```
   Credentials come from `ctx.env.MY_VAR`. Never hard-code them.
4. Register it with `set_project_inputs(journeys=[...existing, {name, steps, spec: ".perf/journeys/<name>.spec.ts"}])`.
5. The worker collects LCP, CLS, INP, TBT, TTFB, FCP, long tasks, JS execution, resources, a HAR, a trace, screenshots, console errors, and Lighthouse.
