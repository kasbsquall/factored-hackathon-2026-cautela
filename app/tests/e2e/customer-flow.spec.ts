import { expect, test, type Page } from "@playwright/test";

async function login(page: Page, scenario: RegExp, enter: string) {
  await page.getByRole("button", { name: scenario }).click();
  await page.getByRole("button", { name: /Enviar código/ }).click();
  await page.getByRole("button", { name: /Usar este código/ }).click();
  await page.getByRole("button", { name: enter, exact: true }).click();
}

test("customer picks one of several charges and gets a verified case, in Spanish", async ({ page }) => {
  await page.goto("/customer");
  await expect(page.getByText("Identidad de prueba")).toBeVisible();
  await login(page, /Descripción vaga/, "Ingresar");

  await page.getByRole("button", { name: /Me cobraron algo/ }).click();
  await expect(page.locator("#conversation ol").getByText(/Encontré más de un cargo que coincide/)).toBeVisible();
  const options = page.getByRole("group", { name: "¿Cuál de estos cargos no reconoces?" }).getByRole("button");
  await expect(options).toHaveCount(4);
  for (const button of await options.all()) await expect(button).toHaveAttribute("aria-pressed", "false");

  await page.getByRole("button", { name: /MERCANUBE/ }).click();
  await expect(page.getByText("Confirma antes de abrir la disputa")).toBeVisible();
  await page.getByRole("button", { name: "Confirmar disputa" }).click();

  const receipt = page.getByRole("region", { name: "Disputa abierta" });
  await expect(receipt).toBeVisible();
  await expect(receipt.getByText(/^CASE-[0-9A-F]{12}$/)).toBeVisible();
  await expect(receipt.getByText("abierta", { exact: true })).toBeVisible();
  await expect(receipt.getByText("CO-WINDOW-001", { exact: true })).toBeVisible();

  await receipt.getByRole("link", { name: "Ver registro técnico (en inglés)" }).click();
  await expect(page.getByText("Hash chain intact")).toBeVisible();
  await expect(page.getByText("open_dispute_case").filter({ visible: true }).first()).toBeVisible();
});

test("a high amount is registered for review and handed to a person, in Portuguese", async ({ page }) => {
  await page.goto("/customer");
  await page.getByRole("button", { name: /PT/ }).click();
  await login(page, /Valor de USD 450 ou mais/, "Entrar");

  await page.getByRole("button", { name: /quase 10 mil pesos/ }).click();
  await expect(page.getByText("Confirme antes de abrir a contestação")).toBeVisible();
  await page.getByRole("button", { name: "Confirmar contestação" }).click();

  const receipt = page.getByRole("region", { name: "Contestação registrada, em revisão" });
  await expect(receipt).toBeVisible();
  await expect(receipt.getByText("SYN-AMOUNT-001", { exact: true })).toBeVisible();
  await expect(receipt.getByText(/^ho_[0-9a-f]{16}$/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Falar com uma pessoa" })).toHaveCount(0);
});
