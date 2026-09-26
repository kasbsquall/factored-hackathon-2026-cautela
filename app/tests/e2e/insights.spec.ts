import { expect, test } from "@playwright/test";

test("insights tells the dispute story with its numbers, caveats and table views", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: /Insights/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Why Cautela starts with unrecognized-charge disputes" })).toBeVisible();
  await expect(page.getByText("of 67,095 complaints", { exact: true })).toBeVisible();
  await expect(page.getByRole("figure")).toHaveCount(10);

  const channels = page.getByRole("figure", { name: "Disputes by reception channel" });
  // Focus reads out the same numbers as hover; retried because focus before hydration has no handler yet.
  await expect(async () => {
    await channels.getByRole("listitem").first().blur();
    await channels.getByRole("listitem").first().focus();
    await expect(channels.getByText(/Call Center: 6,192 of 12,297 complaints, 50\.4%/).first()).toBeVisible({ timeout: 500 });
  }).toPass();
  await channels.getByText("Show the numbers").click();
  await expect(channels.getByRole("table").getByRole("rowheader", { name: "Call Center" })).toBeVisible();

  await expect(page.getByText(/43\.6% first-contact resolution is not a dispute metric/)).toBeVisible();
  await expect(page.getByText(/The dataset is synthetic and templated/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
