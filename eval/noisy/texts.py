"""Team-written customer phrasings for the noisy-customer slice (Spanish and team-generated Portuguese).

Written for this slice from how customers describe a charge they only half remember, not from the service's
parser or lexicon: dates in forms the parser has no pattern for, approximate amounts, misspelled or generic
merchants, chat register. Nothing here is organizer data; the merchant names are the directory already listed in
ml/features/lexicon.py, and every value a message carries (amount, date, merchant, city) is filled in from the
case's own charge by eval/noisy/build.py.

Slots: {what} the kind of charge ("una compra", "un cargo"), {cues} the rendered details, each with its own
leading space or comma.
"""

from __future__ import annotations

OPENERS = {
    "es": ["Hola, me aparece {what}{cues} que no reconozco.",
           "Buenas tardes. No reconozco {what}{cues}, yo no hice eso.",
           "Oigan, revisando mis movimientos vi {what}{cues} y no fui yo.",
           "Quiero reclamar {what}{cues} porque no me suena de nada.",
           "Tengo {what}{cues} en la cuenta que yo no hice."],
    "pt": ["Oi, apareceu {what}{cues} que eu não reconheço.",
           "Boa tarde. Não reconheço {what}{cues}, não fui eu.",
           "Olhando meu extrato vi {what}{cues} e não fui eu que fiz.",
           "Quero contestar {what}{cues}, não faço ideia do que seja.",
           "Tem {what}{cues} na minha conta que eu não fiz."],
}
RESTATE = {  # the answer to "give me more details"
    "es": ["Fue {what}{cues}.", "Te cuento: fue {what}{cues}.", "Es {what}{cues}."],
    "pt": ["Foi {what}{cues}.", "Então, foi {what}{cues}.", "É {what}{cues}."],
}
VAGUE_OPENERS = {  # true but with no detail at all: the details come only when the service asks (wrong_restatement)
    "es": ["Hola, tengo un cobro en mi tarjeta que no reconozco.",
           "Buenas, me apareció un movimiento que yo no hice.",
           "Necesito ayuda, hay algo en mi cuenta que no reconozco.",
           "Me cobraron algo raro y quiero reclamarlo."],
    "pt": ["Oi, tem uma cobrança no meu cartão que eu não reconheço.",
           "Olá, apareceu um movimento que eu não fiz.",
           "Preciso de ajuda, tem algo na minha conta que eu não reconheço.",
           "Me cobraram algo estranho e quero contestar."],
}

WHAT = {  # transaction type -> how the customer names it; None when the type is not part of what they say
    "es": {"Purchase": "una compra", "Withdrawal": "un retiro", "Transfer": "una transferencia", "Payment": "un pago",
           None: "un cargo"},
    "pt": {"Purchase": "uma compra", "Withdrawal": "um saque", "Transfer": "uma transferência",
           "Payment": "um pagamento", None: "uma cobrança"},
}
CHANNEL = {
    "es": {"ATM": " en un cajero", "App": " desde la app", "Web": " por internet",
           "POS": " con la tarjeta en un comercio", "Branch": " en una sucursal", "Transfer": " por transferencia"},
    "pt": {"ATM": " num caixa eletrônico", "App": " pelo aplicativo", "Web": " pela internet",
           "POS": " na maquininha", "Branch": " numa agência", "Transfer": " por transferência"},
}
AT = {"es": " en ", "pt": " em "}
AMOUNT_OF = {"es": " de ", "pt": " de "}
CURRENCY = {"USD": {"es": "dólares", "pt": "dólares"}, "COP": {"es": "pesos", "pt": "pesos"},
            "ARS": {"es": "pesos", "pt": "pesos"}, "MXN": {"es": "pesos", "pt": "pesos"}}
DATE_EXACT = {"es": " el {d} de {month}", "pt": " no dia {d} de {month}"}
MONTHS = {"es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
                 "noviembre", "diciembre"],
          "pt": ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
                 "novembro", "dezembro"]}
MONTHS_SHORT = {"es": ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic"],
                "pt": ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]}
WEEKDAYS = {"es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
            "pt": ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]}

# wrong_date: the customer is sure of a date that is off by a few days, or says it vaguely
DATE_RELATIVE_DAYS = {"es": [" hace como {n} días", " hace unos {n} días", " hará unos {n} días"],
                      "pt": [" há uns {n} dias", " faz uns {n} dias", " tem uns {n} dias"]}
DATE_WEEKDAY = {"es": " el {weekday} pasado",
                "pt": {"segunda": " na segunda passada", "terça": " na terça passada", "quarta": " na quarta passada",
                       "quinta": " na quinta passada", "sexta": " na sexta passada",
                       "sábado": " no sábado passado", "domingo": " no domingo passado"}}

# approx_amount: said with a hedge, or stated flatly as a round figure
APPROX = {"es": ["como {x}", "unos {x}", "más o menos {x}", "alrededor de {x}", "cerca de {x}", "{x}"],
          "pt": ["uns {x}", "mais ou menos {x}", "cerca de {x}", "por volta de {x}", "quase {x}", "{x}"]}
MILLION = {"es": ("millón", "millones"), "pt": ("milhão", "milhões")}

# self_correction: a wrong value, then the true one in the same message or the next
CORRECT_INLINE = {"es": [", no, perdón,", ", no, espera,", ", bueno no,"],
                  "pt": [", não, desculpa,", ", quer dizer,", ", não, pera,"]}
CORRECT_NEXT = {
    "es": {"amount": ["Perdón, me equivoqué con el monto: fueron {true}.", "Corrijo: el monto fue {true}."],
           "date": ["Perdón, me equivoqué de fecha: fue{true}.", "Corrijo la fecha: fue{true}."]},
    "pt": {"amount": ["Desculpa, errei o valor: foram {true}.", "Corrigindo: o valor foi {true}."],
           "date": ["Desculpa, errei a data: foi{true}.", "Corrigindo a data: foi{true}."]},
}

# partial_merchant: misspelled, cut short like a statement descriptor, or named only by what it sells
MERCHANT_MISSPELLED = {
    "Super Ahorro": "Súper Aorro", "Restaurante El Buen Sabor": "restaurant Buen Savor",
    "Tienda Don José": "Tienda Dom José", "Mercado Central": "Mercao Central",
    "Empresa Telefónica": "Empresa Telefonika", "Cable TV": "Cabel TV", "Servicios Públicos": "Servisios Publicos",
    "Internet Plus": "Internet Plux", "Estación de Servicio": "Estacion de Serbicio", "Uber": "Uver",
    "Taxi Seguro": "Taxi Seguru", "Ferretería": "Ferretaria", "Tienda General": "Tienda Jeneral",
    "Cine Premium": "Cine Premiun", "Centro Comercial": "Centro Comersial", "Boutique Moda": "Butique Moda",
    "Streaming Music": "Streming Music", "Conciertos Live": "Consiertos Live", "Teatro Nacional": "Teatro Nasional",
    "Gasolinera Express": "Gasolinera Expres", "Farmacia Salud": "Farmasia Salud", "Clínica Médica": "Clinica Medika",
    "Laboratorio Central": "Laboratorio Sentral", "Óptica Visión": "Óptica Bisión",
}
MERCHANT_GENERIC = {  # merchant -> (es, pt)
    "Super Ahorro": ("un súper", "um mercado"), "Mercado Central": ("un súper", "um mercado"),
    "Tienda General": ("una tiendita", "uma lojinha"), "Tienda Don José": ("una tiendita", "uma lojinha"),
    "Restaurante El Buen Sabor": ("un restaurante", "um restaurante"),
    "Empresa Telefónica": ("la compañía de teléfono", "a operadora de telefone"),
    "Cable TV": ("lo del cable", "a TV a cabo"), "Servicios Públicos": ("una cuenta de luz o agua", "a conta de luz"),
    "Internet Plus": ("el proveedor de internet", "o provedor de internet"),
    "Estación de Servicio": ("una gasolinera", "um posto de gasolina"),
    "Gasolinera Express": ("una gasolinera", "um posto de gasolina"),
    "Uber": ("algo de taxi", "uma corrida de táxi"), "Taxi Seguro": ("algo de taxi", "uma corrida de táxi"),
    "Ferretería": ("una ferretería", "uma loja de ferragens"), "Cine Premium": ("el cine", "o cinema"),
    "Centro Comercial": ("un centro comercial", "um shopping"),
    "Boutique Moda": ("una tienda de ropa", "uma loja de roupas"),
    "Streaming Music": ("una app de música", "um app de música"),
    "Conciertos Live": ("unas entradas para un show", "ingressos de um show"),
    "Teatro Nacional": ("unas entradas para un show", "ingressos de um show"),
    "Farmacia Salud": ("una farmacia", "uma farmácia"), "Clínica Médica": ("una clínica", "uma clínica"),
    "Laboratorio Central": ("un laboratorio", "um laboratório"), "Óptica Visión": ("una óptica", "uma ótica"),
}
MERCHANT_TRUNCATED_FRAME = {"es": [" en algo que dice {x}", " en un lugar que sale como {x}"],
                            "pt": [" em algo que aparece como {x}", " num lugar escrito {x}"]}
MERCHANT_GENERIC_FRAME = {"es": [" en {x}", " en {x}, creo"], "pt": [" em {x}", " em {x}, acho"]}

# chat_style: whatsapp register; values stay true, only the way they are written changes
CHAT_ABBREV = {
    "es": [("por favor", "porfa"), ("porque", "xq"), ("también", "tb"), ("que", "q"), ("para", "pa"),
           ("estoy", "toy"), ("buenas tardes", "buenas"), ("hola", "ola")],
    "pt": [("por favor", "pfv"), ("porque", "pq"), ("também", "tb"), ("você", "vc"), ("não", "n"), ("que", "q"),
           ("para", "pra"), ("está", "ta"), ("boa tarde", "boa")],
}
CHAT_OPENERS = {  # prepended to the lowered message
    "es": ["", "ola ", "oye ", "buenas "],
    "pt": ["", "oi ", "opa ", "ei "],
}
