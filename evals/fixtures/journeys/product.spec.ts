// Persisted deterministic journey: Product details.
import { test, type Page } from '@playwright/test';

type Ctx = { baseURL: string; mark: (name: string) => Promise<void> };

export async function journey(page: Page, ctx: Ctx) {
  await page.goto('/');
  await page.locator('mat-card img').first().waitFor({ timeout: 20000 });
  await page.locator('[aria-label="dismiss cookie message"]').click({ timeout: 4000 }).catch(() => {});
  await page.keyboard.press('Escape');
  await ctx.mark('home');
  await page.locator('mat-card img').first().click();
  await page.locator('mat-dialog-container').first().waitFor({ timeout: 15000 });
  await ctx.mark('product-details');
  await page.keyboard.press('Escape');
}

// Registered only under `npx playwright test`; the perf worker imports `journey` directly.
if (!(globalThis as any).__PERF_RUNNER__) test('ProductDetails', async ({ page, baseURL }) => {
  await journey(page, { baseURL: baseURL ?? 'http://127.0.0.1:3100', mark: async () => {} });
});
