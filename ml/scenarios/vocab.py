"""Generator-side vocabulary for dispute descriptions.

This module belongs to the scenario generator only. Rankers must not import it:
the ranker lexicon lives in ``ml/features/lexicon.py`` and was written from the
seen template families (F1-F4) alone. Held-out vocabulary (families F5 and F6)
exists only here, so a ranker cannot have been tuned on it.

Merchant names are the 24 values found in the organizer transactions. The noun
groups that describe them ("una farmacia") are team-written.
"""

from __future__ import annotations

MERCHANTS = [
    "Super Ahorro", "Restaurante El Buen Sabor", "Tienda Don José", "Mercado Central",
    "Empresa Telefónica", "Cable TV", "Servicios Públicos", "Internet Plus",
    "Estación de Servicio", "Uber", "Taxi Seguro", "Ferretería", "Tienda General",
    "Cine Premium", "Centro Comercial", "Boutique Moda", "Streaming Music",
    "Conciertos Live", "Teatro Nacional", "Gasolinera Express", "Farmacia Salud",
    "Clínica Médica", "Laboratorio Central", "Óptica Visión",
]

# noun group -> merchants it can describe, and surface forms per language.
NOUNS: dict[str, dict] = {
    "supermarket": {"merchants": ["Super Ahorro", "Mercado Central", "Tienda General", "Tienda Don José"],
                    "es": ["un súper", "un supermercado"], "pt": ["um mercado", "um supermercado"],
                    "slang_es": ["el chino", "el mandado"], "slang_pt": ["o mercadinho"]},
    "restaurant": {"merchants": ["Restaurante El Buen Sabor"], "es": ["un restaurante"],
                   "pt": ["um restaurante"], "slang_es": ["un lugar de comida"], "slang_pt": ["um lugar de comida"]},
    "utility": {"merchants": ["Empresa Telefónica", "Cable TV", "Servicios Públicos", "Internet Plus"],
                "es": ["un servicio", "una factura de servicios"], "pt": ["uma conta de serviço"],
                "slang_es": ["un recibo"], "slang_pt": ["um boleto de conta"]},
    "fuel": {"merchants": ["Estación de Servicio", "Gasolinera Express"], "es": ["una gasolinera"],
             "pt": ["um posto de gasolina"], "slang_es": ["la bomba de nafta", "cargar gasolina"],
             "slang_pt": ["abastecer o carro"]},
    "ride": {"merchants": ["Uber", "Taxi Seguro"], "es": ["un taxi", "un viaje en taxi"],
             "pt": ["uma corrida de táxi"], "slang_es": ["un remis"], "slang_pt": ["uma corrida"]},
    "hardware": {"merchants": ["Ferretería"], "es": ["una ferretería"], "pt": ["uma loja de ferragens"],
                 "slang_es": ["la tlapalería"], "slang_pt": ["o depósito de construção"]},
    "clothing": {"merchants": ["Boutique Moda"], "es": ["una tienda de ropa"], "pt": ["uma loja de roupas"],
                 "slang_es": ["unas pilchas"], "slang_pt": ["umas roupas"]},
    "mall": {"merchants": ["Centro Comercial"], "es": ["un centro comercial"], "pt": ["um shopping"],
             "slang_es": ["el shopping"], "slang_pt": ["o shopping center"]},
    "entertainment": {"merchants": ["Cine Premium", "Conciertos Live", "Teatro Nacional"],
                      "es": ["unas entradas", "un espectáculo"], "pt": ["uns ingressos"],
                      "slang_es": ["unos boletos para un show"], "slang_pt": ["um show"]},
    "subscription": {"merchants": ["Streaming Music"], "es": ["una suscripción"], "pt": ["uma assinatura"],
                     "slang_es": ["una plataforma de música"], "slang_pt": ["um app de música"]},
    "pharmacy": {"merchants": ["Farmacia Salud"], "es": ["una farmacia"], "pt": ["uma farmácia"],
                 "slang_es": ["la farma"], "slang_pt": ["a drogaria"]},
    "health": {"merchants": ["Clínica Médica", "Laboratorio Central", "Óptica Visión", "Farmacia Salud"],
               "es": ["algo médico", "una consulta médica"], "pt": ["uma consulta médica"],
               "slang_es": ["unos estudios"], "slang_pt": ["uns exames"]},
}

MERCHANT_NOUNS: dict[str, list[str]] = {}
for _key, _spec in NOUNS.items():
    for _m in _spec["merchants"]:
        MERCHANT_NOUNS.setdefault(_m, []).append(_key)

CHANNEL_ES = {
    "ATM": {"MX": ["en un cajero"], "CO": ["en un cajero"], "AR": ["en un cajero"]},
    "App": {"MX": ["por la app"], "CO": ["por la app"], "AR": ["desde la app"]},
    "Web": {"MX": ["por internet", "en la página web"], "CO": ["por internet"], "AR": ["por la web"]},
    "POS": {"MX": ["con la tarjeta en una terminal"], "CO": ["en un datáfono"], "AR": ["con la tarjeta en un posnet"]},
    "Branch": {"MX": ["en la sucursal"], "CO": ["en la oficina del banco"], "AR": ["en la sucursal"]},
    "Transfer": {"MX": ["por transferencia"], "CO": ["por transferencia"], "AR": ["por transferencia"]},
}
CHANNEL_PT = {
    "ATM": ["no caixa eletrônico"], "App": ["pelo aplicativo"], "Web": ["pela internet", "no site"],
    "POS": ["na maquininha"], "Branch": ["na agência"], "Transfer": ["por transferência"],
}
TYPE_ES = {
    "Purchase": {"MX": "una compra", "CO": "una compra", "AR": "una compra"},
    "Withdrawal": {"MX": "un retiro", "CO": "un retiro", "AR": "una extracción"},
    "Transfer": {"MX": "una transferencia", "CO": "una transferencia", "AR": "una transferencia"},
    "Payment": {"MX": "un pago", "CO": "un pago", "AR": "un pago"},
    "Adjustment": {"MX": "un ajuste", "CO": "un ajuste", "AR": "un ajuste"},
}
TYPE_PT = {"Purchase": "uma compra", "Withdrawal": "um saque", "Transfer": "uma transferência",
           "Payment": "um pagamento", "Adjustment": "um ajuste"}

WEEKDAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
WEEKDAYS_PT = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
             "octubre", "noviembre", "diciembre"]
MONTHS_PT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
             "outubro", "novembro", "dezembro"]

APPROX_ES = ["como", "unos", "más o menos", "alrededor de", "casi"]
APPROX_PT = ["uns", "cerca de", "mais ou menos", "quase"]

# Held-out (F6) number words; the ranker parser has no number-word support.
NUMBER_WORDS_ES = {100: "cien", 200: "doscientos", 300: "trescientos", 400: "cuatrocientos", 500: "quinientos",
                   600: "seiscientos", 700: "setecientos", 800: "ochocientos", 900: "novecientos"}
NUMBER_WORDS_PT = {100: "cem", 200: "duzentos", 300: "trezentos", 400: "quatrocentos", 500: "quinhentos",
                   600: "seiscentos", 700: "setecentos", 800: "oitocentos", 900: "novecentos"}
