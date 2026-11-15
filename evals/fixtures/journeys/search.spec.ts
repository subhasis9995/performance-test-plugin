// Persisted deterministic journey: Search. Generated with Playwright MCP exploration, then frozen.
// Run by the perf browser worker (exports `journey`) or by developers: `npx playwright test search.spec.ts`.
import { test, type Page } from '@playwright/test';

export const lighthouseUrl = '/#/search?q=apple';

type Ctx = { baseURL: string; mark: (name: string) => Promise<void> };

async function dismissOverlays(page: Page) {
  await page.locator('[aria-label="dismiss cookie message"]').click({ timeout: 4000 }).catch(() => {});
  await page.keyboard.press('Escape'); // welcome banner dialog
}

export async function journey(page: Page, ctx: Ctx) {
  await page.goto('/');
  await page.locator('mat-card').first().waitFor({ timeout: 20000 });
  await ctx.mark('home');
  await dismissOverlays(page);
  await page.locator('#searchQuery').click();
  await page.locator('#searchQuery input').fill('apple');
  await page.keyboard.press('Enter');
  await page.waitForURL(/search\?q=apple/, { timeout: 15000 });
  await page.locator('app-search-result mat-card').first().waitFor({ timeout: 20000 });
  await ctx.mark('results');
}

// Registered only under `npx playwright test`; the perf worker imports `journey` directly.
if (!(globalThis as any).__PERF_RUNNER__) test('Search', async ({ page, baseURL }) => {
  await journey(page, { baseURL: baseURL ?? 'http://127.0.0.1:3100', mark: async () => {} });
});
