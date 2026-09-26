/**
 * English versions of the mock identities' suggested messages, written with the fixtures (customers.ts). Mock mode has
 * no language model, so these are the only customer texts it can show in English; anything else typed in mock mode
 * answers translation_unavailable, as the live service does without a model.
 */
export const AUTHORED_ENGLISH: Record<string, string> = {
  "Me cobraron algo que no reconozco, como 50 mil pesos, el domingo o el lunes.":
    "I was charged something I do not recognize, about 50 thousand pesos, on Sunday or Monday.",
  "Me cobraram algo que não reconheço, uns 50 mil pesos, no domingo ou na segunda.":
    "I was charged something I do not recognize, about 50 thousand pesos, on Sunday or Monday.",
  "Tengo un cargo de casi 10 mil pesos de una agencia de viajes que no hice.":
    "I have a charge of almost 10 thousand pesos from a travel agency that I did not make.",
  "Tenho uma cobrança de quase 10 mil pesos de uma agência de viagens que eu não fiz.":
    "I have a charge of almost 10 thousand pesos from a travel agency that I did not make.",
  "No reconozco un cobro de unos 85 mil pesos del viernes a la noche.":
    "I do not recognize a charge of about 85 thousand pesos from Friday night.",
  "Não reconheço uma cobrança de uns 85 mil pesos de sexta à noite.":
    "I do not recognize a charge of about 85 thousand pesos from Friday night.",
};
