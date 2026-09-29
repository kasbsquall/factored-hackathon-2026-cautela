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

  const mercanube = page.getByRole("button", { name: /MERCANUBE/ });
  await expect(mercanube.getByRole("list", { name: "En qué coincide" }).getByRole("listitem")).toHaveCount(2);
  await mercanube.click();
  await expect(page.getByRole("heading", { name: "¿Reconoces este cargo?" })).toBeVisible();
  await expect(page.getByText("Débito •••• 4821").last()).toBeVisible();
  await page.getByRole("button", { name: /No lo reconozco/ }).click();
  await expect(page.getByText("Confirma antes de abrir la disputa")).toBeVisible();
  await page.getByRole("button", { name: "Confirmar disputa" }).click();

  const receipt = page.getByRole("region", { name: "Disputa abierta" });
  await expect(receipt).toBeVisible();
  await expect(receipt.getByText(/^CASE-[0-9A-F]{12}$/).first()).toBeVisible();
  await expect(receipt.getByText("abierta", { exact: true })).toBeVisible();
  await expect(receipt.getByText("CO-WINDOW-001", { exact: true })).toBeVisible();
  await expect(receipt.getByRole("region", { name: "Lo que verificamos" }).getByRole("listitem")).toHaveCount(5);
  await expect(page.getByRole("region", { name: "No tienes que hacer nada más" })
    .getByRole("button", { name: "Que una persona siga con mi caso" })).toBeVisible();

  await receipt.getByRole("link", { name: "Ver registro técnico (en inglés)" }).click();
  await expect(page.getByText("Hash chain intact")).toBeVisible();
  await expect(page.getByText("open_dispute_case").filter({ visible: true }).first()).toBeVisible();
});

/** A stand-in speechSynthesis with one on-device voice per language; what it is asked to say lands in window.__spoken. */
function fakeSpeech() {
  const voices = ["es-MX", "pt-BR", "en-US"].map((lang) => ({ lang, localService: true, name: `Test ${lang}`, default: false, voiceURI: lang }));
  const spoken: string[] = [];
  Object.assign(window, { __spoken: spoken });
  const synth = {
    getVoices: () => voices,
    speak: (u: { text: string }) => spoken.push(u.text),
    cancel: () => undefined, pause: () => undefined, resume: () => undefined,
    addEventListener: () => undefined, removeEventListener: () => undefined,
  };
  Object.defineProperty(window, "speechSynthesis", { value: synth, configurable: true });
  Object.defineProperty(window, "SpeechSynthesisUtterance", {
    value: class { voice = null; lang = ""; constructor(public text: string) {} }, configurable: true,
  });
}

test("the customer can keep the receipt: copy the case number and hear what was checked", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.addInitScript(fakeSpeech);
  await page.goto("/customer");
  await login(page, /Descripción vaga/, "Ingresar");

  await expect(page.getByRole("switch", { name: "Leer respuestas en voz alta" })).toHaveAttribute("aria-checked", "false");
  await page.getByRole("button", { name: /Me cobraron algo/ }).click();
  await page.getByRole("button", { name: /MERCANUBE/ }).click();
  await page.getByRole("button", { name: /No lo reconozco/ }).click();
  await page.getByRole("button", { name: "Confirmar disputa" }).click();

  const receipt = page.getByRole("region", { name: "Disputa abierta" });
  const caseId = (await receipt.getByText(/^CASE-[0-9A-F]{12}$/).first().textContent()) ?? "";
  await receipt.getByRole("button", { name: "Copiar número" }).click();
  await expect(receipt.getByRole("button", { name: "Copiado" })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(caseId);

  await receipt.getByRole("button", { name: "Escuchar el comprobante" }).click();
  await expect(receipt.getByRole("button", { name: "Pausar" })).toBeVisible();
  const said = await page.evaluate(() => (window as unknown as { __spoken: string[] }).__spoken.at(-1) ?? "");
  expect(said).toContain("Lo que verificamos");
  expect(said).toContain("Volvimos a leer el caso en el sistema del banco");
  expect(said).toContain("tarjeta de débito terminada en 4821");
  expect(said).not.toContain("Copiar número");
});

test("after filing, a person takes over with the case number and the checked facts", async ({ page }) => {
  await page.goto("/customer");
  await login(page, /Descripción vaga/, "Ingresar");
  await page.getByRole("button", { name: /Me cobraron algo/ }).click();
  await page.getByRole("button", { name: /MERCANUBE/ }).click();
  await page.getByRole("button", { name: /No lo reconozco/ }).click();
  await page.getByRole("button", { name: "Confirmar disputa" }).click();
  const caseId = (await page.getByRole("region", { name: "Disputa abierta" }).getByText(/^CASE-[0-9A-F]{12}$/).first().textContent()) ?? "";

  await page.getByRole("button", { name: "Que una persona siga con mi caso" }).click();
  const takeover = page.getByRole("region", { name: "Una persona seguirá con tu caso" });
  await expect(takeover).toBeVisible();
  await expect(takeover.getByText(caseId, { exact: true })).toBeVisible();
  await expect(takeover.getByText(/^ho_[0-9a-f]{16}$/)).toBeVisible();
  await expect(takeover.getByRole("region", { name: "Lo que verificamos" }).getByRole("listitem")).toHaveCount(5);
  await expect(page.locator("#conversation ol").getByText(new RegExp(`Te paso con una persona del banco.*${caseId}`))).toBeVisible();
  await expect(page.getByRole("button", { name: "Que una persona siga con mi caso" })).toHaveCount(0);
});

test("a high amount is registered for review and handed to a person, in Portuguese", async ({ page }) => {
  await page.goto("/customer");
  await page.getByRole("button", { name: /PT/ }).click();
  await login(page, /Valor de USD 450 ou mais/, "Entrar");

  await page.getByRole("button", { name: /quase 10 mil pesos/ }).click();
  await expect(page.getByRole("heading", { name: "Você reconhece esta cobrança?" })).toBeVisible();
  await page.getByRole("button", { name: /Não reconheço/ }).click();
  await expect(page.getByText("Confirme antes de abrir a contestação")).toBeVisible();
  await page.getByRole("button", { name: "Confirmar contestação" }).click();

  const receipt = page.getByRole("region", { name: "Contestação registrada, em revisão" });
  await expect(receipt).toBeVisible();
  await expect(receipt.getByText("SYN-AMOUNT-001", { exact: true })).toBeVisible();
  await expect(receipt.getByText(/^ho_[0-9a-f]{16}$/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Falar com uma pessoa" })).toHaveCount(0);
});

test("a customer who recognizes the charge stops before any dispute", async ({ page }) => {
  await page.goto("/customer");
  await login(page, /Valor de USD 450|Monto de USD 450/, "Ingresar");

  await page.getByRole("button", { name: /casi 10 mil pesos/ }).click();
  await expect(page.getByRole("heading", { name: "¿Reconoces este cargo?" })).toBeVisible();
  await page.getByRole("button", { name: "Sí, lo reconozco" }).click();

  const receipt = page.getByRole("region", { name: "Sin disputa" });
  await expect(receipt).toBeVisible();
  await expect(receipt.getByText("ninguno", { exact: true })).toBeVisible();
  await expect(page.getByText("Confirma antes de abrir la disputa")).toHaveCount(0);
});

test("reviewer view: English labels, narration and translations while the conversation stays in Spanish", async ({ page }) => {
  await page.goto("/customer?review=en");
  await expect(page.getByText("Test identity")).toBeVisible();
  await expect(page.getByRole("button", { name: /EN reviewer view/ })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: /Vague description/ }).click();
  await page.getByRole("button", { name: /Send code/ }).click();
  await page.getByRole("button", { name: /Use this code/ }).click();
  await page.getByRole("button", { name: "Log in", exact: true }).click();

  await page.getByRole("button", { name: /Me cobraron algo/ }).click();
  const log = page.locator("#conversation ol").first();
  await expect(log.getByText(/Encontré más de un cargo que coincide/)).toBeVisible();
  await expect(page.getByRole("group", { name: "Which of these charges do you not recognize?" })).toBeVisible();

  // Narration: collapsed under the chat on a phone, English, built from the trail.
  const disclose = page.getByRole("button", { name: /Show the explanation/ });
  await expect(disclose).toContainText("1 turn");
  await disclose.click();
  const narration = page.getByRole("region", { name: "What the system did (for reviewers)" });
  await expect(narration.getByText(/Found 3 candidate charges that fit the description/)).toBeVisible();
  await expect(narration.getByText(/None was preselected; the customer must choose/)).toBeVisible();

  // Translations: the reply template is translated without a model; the test message has its written English.
  const toggles = log.getByRole("button", { name: "Show English translation" });
  await toggles.last().click();
  await expect(log.getByText("I found more than one charge that fits. Which one do you not recognize?", { exact: false })).toBeVisible();
  await expect(log.getByText(/fixed reply template "clarify_options"/)).toBeVisible();
  await log.getByRole("button", { name: "Show English translation" }).nth(1).click();
  await expect(log.getByText(/about 50 thousand pesos, on Sunday or Monday/)).toBeVisible();

  // "None of these" is labeled in English but sent in Spanish.
  await page.getByRole("button", { name: "None of these" }).click();
  await expect(log.getByText("Ninguno de estos", { exact: true })).toBeVisible();

  // Turning the reviewer view off brings the Spanish labels back; the conversation does not change.
  await page.getByRole("button", { name: /EN reviewer view/ }).click();
  await expect(page.getByRole("button", { name: "Hablar con una persona" }).first()).toBeVisible();
  await expect(log.getByText(/Encontré más de un cargo que coincide/)).toBeVisible();
});
