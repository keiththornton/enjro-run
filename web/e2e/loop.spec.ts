import { expect, test } from "@playwright/test";

const MODEL = "test--tiny-chat--q4_k_m";

// Every test starts with the seeded model unloaded.
test.beforeEach(async ({ request }) => {
  await request.post(`/admin/models/${MODEL}/unload`, { data: {} });
});

test("load a model, chat in the playground, see the request, replay it", async ({ page }) => {
  // Models: the seeded model sits on disk; load it with the live estimate.
  await page.goto("/#/models");
  await expect(page.getByText("tiny-chat:q4_k_m")).toBeVisible();
  await page.getByRole("button", { name: "Load", exact: true }).click();
  const sheet = page.getByRole("dialog");
  await expect(sheet.getByText("Fits")).toBeVisible();
  await expect(sheet.getByLabel("Parallel requests")).toHaveValue("2");
  await sheet.getByRole("button", { name: "Load", exact: true }).click();
  await expect(page.getByRole("button", { name: "Unload" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("img", { name: "Serving" })).toBeVisible();

  // Playground: streaming reply with metrics.
  await page.getByRole("button", { name: "Playground" }).click();
  await page.getByLabel("Message").fill("Say something");
  await page.getByLabel("Message").press("Enter");
  await expect(page.locator(".turn-assistant .turn-text")).toContainText("tok0 tok1");
  await expect(page.locator(".turn-assistant .turn-stats")).toContainText("tok/s");

  // Requests: the chat arrived live with its metrics.
  await page.getByRole("button", { name: "Requests" }).click();
  const row = page.locator(".rack-row").first();
  await expect(row).toContainText("tiny-chat:q4_k_m");
  await expect(row).toContainText("stream");
  await expect(row).toContainText("7→5");

  // Detail sheet → replay into the playground.
  await row.click();
  await expect(page.getByRole("dialog").getByText('"content": "Say something"')).toBeVisible();
  await page.getByRole("button", { name: "Replay in playground" }).click();
  await expect(page.locator(".turn-user")).toHaveText("Say something");
  await expect(page.getByRole("button", { name: "Run" })).toBeEnabled();
});

test("no horizontal overflow on any page", async ({ page }) => {
  for (const p of ["models", "discover", "requests", "playground", "api", "settings"]) {
    await page.goto(`/#/${p}`);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `${p} overflows horizontally`).toBeLessThanOrEqual(0);
  }
});

test("API page shows this server's URL and copy-ready code; Discover lists runnable models", async ({ page, baseURL }) => {
  await page.goto("/#/api");
  await expect(page.getByText(`${baseURL}/v1`).first()).toBeVisible();
  await page.getByRole("button", { name: "curl" }).click();
  await page.getByRole("button", { name: "Embeddings" }).click();
  await expect(page.getByLabel("curl example: embeddings", { exact: true })).toContainText(`curl -sS ${baseURL}/v1/embeddings`);
  await expect(page.getByRole("cell", { name: "model_not_loaded" })).toBeVisible();

  await page.goto("/#/discover");
  await expect(page.getByText("Llama-3.2-3B-Instruct-GGUF")).toBeVisible();
  await expect(page.getByText("3B", { exact: true })).toBeVisible();
});
