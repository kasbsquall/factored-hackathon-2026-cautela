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
  // One tab stop per chart: arrow keys move between its marks.
  await page.keyboard.press("ArrowDown");
  await expect(channels.getByRole("listitem").nth(1)).toBeFocused();
  await expect(channels.getByRole("listitem").first()).toHaveAttribute("tabindex", "-1");
  await channels.getByText("Show the numbers").click();
  await expect(channels.getByRole("table").getByRole("rowheader", { name: "Call Center" })).toBeVisible();

  await expect(page.getByText(/43\.6% first-contact resolution is not a dispute metric/)).toBeVisible();
  await expect(page.getByText(/The dataset is synthetic and templated/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("the home links to how Cautela decides, with its thresholds and claim windows", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: /How Cautela decides/ }).click();
  const section = page.locator("#decides");
  await expect(section.getByRole("heading", { level: 2, name: "How Cautela decides" })).toBeInViewport();
  await expect(section.getByRole("region", { name: "Thresholds" }).getByText("USD 450", { exact: true })).toBeAttached();
  await expect(section.getByRole("region", { name: "Claim windows" }).getByRole("row")).toHaveCount(6);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
