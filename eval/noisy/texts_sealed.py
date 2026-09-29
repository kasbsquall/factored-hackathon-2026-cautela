"""Second, independent set of customer phrasings, used only for the sealed half of the noisy-customer slice.

Written blind to the dev results, so the sealed half tests new phrasings as well as new charges: other correction
markers, other hedges for amounts and dates, other chat abbreviations, other misspellings and generic names for the
same merchant directory. Spanish as written in Mexico, Colombia and Argentina; Portuguese as written by a Brazilian
living in those countries, who still counts in pesos. Same names, shapes and slots as eval/noisy/texts.py, so
eval/noisy/build.py can swap one module for the other; the merchant directory (the keys of MERCHANT_MISSPELLED and
MERCHANT_GENERIC) is the same, so the draw of cases does not depend on the template set.

Slots: {what} the kind of charge, {cues} the rendered details, each with its own leading space or comma.
"""

from __future__ import annotations

OPENERS = {
    "es": ["Buen día, tengo {what}{cues} que no hice yo.",
           "Qué tal, me llegó {what}{cues} y yo no fui.",
           "Hola, ¿me ayudan? Me salió {what}{cues} y no tengo idea de qué es.",
           "Disculpen, en el extracto me figura {what}{cues} que desconozco.",
           "Buenas noches, les escribo por {what}{cues} que yo no autoricé.",
           "Che, me cobraron {what}{cues} y no fui yo.",
           "Hola, vengo a desconocer {what}{cues}."],
    "pt": ["Bom dia, tem {what}{cues} que eu não fiz.",
           "E aí, pessoal, caiu {what}{cues} na minha conta e não fui eu.",
           "Oi, gente, preciso contestar {what}{cues} que eu não fiz.",
           "Olá, vi {what}{cues} aqui na fatura e não reconheço de jeito nenhum.",
           "Boa noite, estou com {what}{cues} que eu nunca fiz.",
           "Com licença, alguém pode ver {what}{cues}? Não fui eu.",
           "Oi, apareceu no extrato {what}{cues} e eu não sei o que é."],
}
RESTATE = {  # the answer to "give me more details"
    "es": ["Mira, era {what}{cues}.", "Sí, claro: {what}{cues}.", "Lo que vi fue {what}{cues}.",
           "Dale, era {what}{cues}."],
    "pt": ["Olha, era {what}{cues}.", "Claro: {what}{cues}.", "O que eu vi foi {what}{cues}.",
           "Beleza, era {what}{cues}."],
}
VAGUE_OPENERS = {  # true but with no detail at all: the details come only when the service asks (wrong_restatement)
    "es": ["Buen día, quiero desconocer un movimiento de mi tarjeta.",
           "Hola, me salió un cobro raro y no fui yo.",
           "Qué tal, necesito reportar algo que no reconozco en mi cuenta.",
           "Disculpen, hay un cargo que yo no autoricé.",
           "Che, me aparece algo en el resumen que no es mío."],
    "pt": ["Bom dia, quero contestar um lançamento no meu cartão.",
           "Oi, apareceu uma cobrança esquisita e não fui eu.",
           "Olá, preciso reportar uma coisa que não reconheço na conta.",
           "Com licença, tem um lançamento que eu não autorizei.",
           "Gente, veio um débito na fatura que não é meu."],
}

WHAT = {  # transaction type -> how the customer names it; None when the type is not part of what they say
    "es": {"Purchase": "un consumo", "Withdrawal": "un retiro de efectivo", "Transfer": "una transferencia",
           "Payment": "un pago", None: "un cobro"},
    "pt": {"Purchase": "uma compra", "Withdrawal": "um saque", "Transfer": "uma transferência",
           "Payment": "um pagamento", None: "um lançamento"},
}
CHANNEL = {
    "es": {"ATM": " en un cajero automático", "App": " por la aplicación", "Web": " en la página web",
           "POS": " pagando con tarjeta en un local", "Branch": " en una oficina del banco",
           "Transfer": " vía transferencia"},
    "pt": {"ATM": " no caixa automático", "App": " pelo app", "Web": " pelo site",
           "POS": " passando o cartão numa loja", "Branch": " na agência do banco",
           "Transfer": " via transferência"},
}
AT = {"es": " en ", "pt": " em "}
AMOUNT_OF = {"es": " por ", "pt": " de "}
CURRENCY = {"USD": {"es": "dólares", "pt": "dólares"}, "COP": {"es": "pesos", "pt": "pesos"},
            "ARS": {"es": "pesos", "pt": "pesos"}, "MXN": {"es": "pesos", "pt": "pesos"}}
DATE_EXACT = {"es": " el día {d} de {month}", "pt": " dia {d} de {month}"}
MONTHS = {"es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
                 "noviembre", "diciembre"],
          "pt": ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
                 "novembro", "dezembro"]}
MONTHS_SHORT = {"es": ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"],
                "pt": ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]}
WEEKDAYS = {"es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
            "pt": ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]}

# wrong_date: the customer is sure of a date that is off by a few days, or says it vaguely
DATE_RELATIVE_DAYS = {"es": [" hace {n} días más o menos", " va para {n} días", " hace {n} días, por ahí",
                             " unos {n} días atrás"],
                      "pt": [" uns {n} dias atrás", " faz {n} dias mais ou menos", " há coisa de {n} dias",
                             " vai fazer {n} dias"]}
DATE_WEEKDAY = {"es": " el {weekday} que pasó",  # same meaning as "el lunes pasado": the most recent one
                "pt": {"segunda": " na segunda que passou", "terça": " na terça que passou",
                       "quarta": " na quarta que passou", "quinta": " na quinta que passou",
                       "sexta": " na sexta que passou", "sábado": " no sábado que passou",
                       "domingo": " no domingo que passou"}}

# approx_amount: said with a hedge, or stated flatly as a round figure
APPROX = {"es": ["tipo {x}", "algo así como {x}", "aproximadamente {x}", "{x} más o menos", "{x} o por ahí",
                 "un aproximado de {x}", "{x} redondos"],
          "pt": ["tipo {x}", "uns {x} e pouco", "algo em torno de {x}", "aproximadamente {x}", "{x} mais ou menos",
                 "{x} por aí", "{x} certinho"]}
MILLION = {"es": ("millón", "millones"), "pt": ("milhão", "milhões")}

# self_correction: a wrong value, then the true one in the same message or the next
CORRECT_INLINE = {"es": [", mentira,", ", digo,", ", mejor dicho,", ", ay no, me confundí,"],
                  "pt": [", aliás,", ", ops, errei,", ", ou melhor,", ", não não,"]}
CORRECT_NEXT = {
    "es": {"amount": ["Me corrijo: en realidad fueron {true}.", "Uy, el monto está mal, eran {true}.",
                      "Ojo que me equivoqué, el valor real es {true}."],
           "date": ["Me corrijo: en realidad fue{true}.", "Uy, la fecha está mal, fue{true}.",
                    "Ojo que me confundí de día, fue{true}."]},
    "pt": {"amount": ["Me enganei no valor, na verdade foram {true}.", "Ops, o valor certo é {true}.",
                      "Retificando: foram {true}."],
           "date": ["Me enganei na data, na verdade foi{true}.", "Ops, a data certa é{true}.",
                    "Retificando: foi{true}."]},
}

# partial_merchant: misspelled, cut short like a statement descriptor, or named only by what it sells
MERCHANT_MISSPELLED = {
    "Super Ahorro": "Superahorro", "Restaurante El Buen Sabor": "El Buensabor",
    "Tienda Don José": "Tienda D. José", "Mercado Central": "Merkado Central",
    "Empresa Telefónica": "Empresa Telefónca", "Cable TV": "Kable TV", "Servicios Públicos": "Servicio Publicos",
    "Internet Plus": "Interned Plus", "Estación de Servicio": "Estasión de Servicio", "Uber": "Ubber",
    "Taxi Seguro": "Taxy Seguro", "Ferretería": "Ferretría", "Tienda General": "Tienda Gral",
    "Cine Premium": "Cine Primium", "Centro Comercial": "Centro Comercal", "Boutique Moda": "Butic Moda",
    "Streaming Music": "Streaming Musik", "Conciertos Live": "Conciertos Laif", "Teatro Nacional": "Tiatro Nacional",
    "Gasolinera Express": "Gasolineria Express", "Farmacia Salud": "Farmacia Salú",
    "Clínica Médica": "Clínika Médica",
    "Laboratorio Central": "Labortorio Central", "Óptica Visión": "Obtica Vision",
}
MERCHANT_GENERIC = {  # merchant -> (es, pt)
    "Super Ahorro": ("el supermercado", "um supermercado"), "Mercado Central": ("el supermercado", "um supermercado"),
    "Tienda General": ("una tienda de barrio", "uma vendinha"),
    "Tienda Don José": ("una tienda de barrio", "uma vendinha"),
    "Restaurante El Buen Sabor": ("un lugar de comida", "um lugar de comida"),
    "Empresa Telefónica": ("la empresa del celular", "uma operadora de celular"),
    "Cable TV": ("la tele por cable", "uma assinatura de TV"),
    "Servicios Públicos": ("el pago de los servicios", "uma conta de serviços"),
    "Internet Plus": ("lo del internet", "uma conta de internet"),
    "Estación de Servicio": ("una bomba de gasolina", "um posto"),
    "Gasolinera Express": ("una bomba de gasolina", "um posto"),
    "Uber": ("un viaje de app", "uma corrida de aplicativo"),
    "Taxi Seguro": ("un viaje de app", "uma corrida de aplicativo"),
    "Ferretería": ("una tienda de herramientas", "uma loja de material de construção"),
    "Cine Premium": ("unas entradas de cine", "ingressos de cinema"),
    "Centro Comercial": ("un shopping", "um shopping center"),
    "Boutique Moda": ("un local de ropa", "uma loja de moda"),
    "Streaming Music": ("una suscripción de música", "uma assinatura de música"),
    "Conciertos Live": ("unos boletos para un evento", "ingressos pra um evento"),
    "Teatro Nacional": ("unos boletos para un evento", "ingressos pra um evento"),
    "Farmacia Salud": ("una droguería", "uma drogaria"),
    "Clínica Médica": ("una consulta médica", "uma consulta médica"),
    "Laboratorio Central": ("unos exámenes de laboratorio", "uns exames de laboratório"),
    "Óptica Visión": ("unos lentes", "uns óculos"),
}
MERCHANT_TRUNCATED_FRAME = {"es": [" en un comercio que figura como {x}", " en algo que en el resumen dice {x}",
                                   " en un tal {x}"],
                            "pt": [" num estabelecimento que aparece {x}", " em um tal de {x}",
                                   " em algo que no extrato vem como {x}"]}
MERCHANT_GENERIC_FRAME = {"es": [" en {x}, me parece", " en {x}, si no me equivoco", " en {x} o algo así"],
                          "pt": [" em {x}, se não me engano", " em {x}, eu acho", " em {x} ou coisa assim"]}

# chat_style: whatsapp register; values stay true, only the way they are written changes. The long forms are words
# of the openers and restatements above, never words of a value (merchant, city, month, currency, type, channel).
CHAT_ABBREV = {
    "es": [("quiero", "kiero"), ("tengo", "tngo"), ("hice", "ise"), ("desconozco", "desconosco"),
           ("qué tal", "q tal"), ("buen día", "bn dia"), ("buenas noches", "bns noches"), ("ustedes", "uds"),
           ("idea", "idia")],
    "pt": [("aqui", "aki"), ("gente", "gnt"), ("estou", "to"), ("nunca", "nunk"), ("com licença", "cmlc"),
           ("nenhum", "nenhu"), ("pessoal", "pessoall"), ("beleza", "blz"), ("bom dia", "bdia")],
}
CHAT_OPENERS = {  # prepended to the lowered message
    "es": ["", "che ", "quiubo ", "hey ", "alo "],
    "pt": ["", "eai ", "fala ", "salve ", "gnt "],
}
