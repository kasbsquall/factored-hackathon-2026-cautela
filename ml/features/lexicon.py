"""Ranker-side lexicon.

Written from the seen template families (F1-F4) only, in Spanish and Portuguese,
and kept separate from the generator vocabulary on purpose. Nothing from the
held-out families (F5 formal letters, F6 slang and number words) is listed
here, so held-out results measure how the rankers cope with unseen phrasing.

The noun-to-merchant map stands in for the merchant descriptors (MCC groups) a
bank already holds; it is team-written.
"""

from __future__ import annotations

# normalized (lowercase, no accents) noun phrase -> merchants it can describe
NOUN_MERCHANTS: dict[str, tuple[str, ...]] = {
    "super": ("Super Ahorro", "Mercado Central", "Tienda General", "Tienda Don José"),
    "supermercado": ("Super Ahorro", "Mercado Central", "Tienda General", "Tienda Don José"),
    "mercado": ("Super Ahorro", "Mercado Central", "Tienda General", "Tienda Don José"),
    "restaurante": ("Restaurante El Buen Sabor",),
    "servicio": ("Empresa Telefónica", "Cable TV", "Servicios Públicos", "Internet Plus"),
    "factura de servicios": ("Empresa Telefónica", "Cable TV", "Servicios Públicos", "Internet Plus"),
    "conta de servico": ("Empresa Telefónica", "Cable TV", "Servicios Públicos", "Internet Plus"),
    "gasolinera": ("Estación de Servicio", "Gasolinera Express"),
    "posto de gasolina": ("Estación de Servicio", "Gasolinera Express"),
    "taxi": ("Uber", "Taxi Seguro"),
    "ferreteria": ("Ferretería",),
    "ferragens": ("Ferretería",),
    "tienda de ropa": ("Boutique Moda",),
    "loja de roupas": ("Boutique Moda",),
    "centro comercial": ("Centro Comercial",),
    "shopping": ("Centro Comercial",),
    "entradas": ("Cine Premium", "Conciertos Live", "Teatro Nacional"),
    "espectaculo": ("Cine Premium", "Conciertos Live", "Teatro Nacional"),
    "ingressos": ("Cine Premium", "Conciertos Live", "Teatro Nacional"),
    "suscripcion": ("Streaming Music",),
    "assinatura": ("Streaming Music",),
    "farmacia": ("Farmacia Salud",),
    "medico": ("Clínica Médica", "Laboratorio Central", "Óptica Visión", "Farmacia Salud"),
    "consulta medica": ("Clínica Médica", "Laboratorio Central", "Óptica Visión", "Farmacia Salud"),
}

TYPE_WORDS = {
    "Purchase": ("compra",),
    "Withdrawal": ("retiro", "extraccion", "saque"),
    "Transfer": ("una transferencia", "uma transferencia"),
    "Payment": ("pago", "pagamento"),
    "Adjustment": ("ajuste",),
}

CHANNEL_WORDS = {
    "ATM": ("cajero", "caixa eletronico"),
    "App": ("app", "aplicativo"),
    "Web": ("internet", "pagina web", "la web", "site"),
    "POS": ("terminal", "datafono", "posnet", "maquininha"),
    "Branch": ("sucursal", "oficina del banco", "agencia"),
    "Transfer": ("por transferencia",),
}

CURRENCY_WORDS = {"USD": ("dolares", "dolar", "usd", "dlls", "dls"), "PESOS": ("pesos",)}
MULTIPLIERS = {"mil": 1_000, "k": 1_000, "millon": 1_000_000, "millones": 1_000_000,
               "milhao": 1_000_000, "milhoes": 1_000_000}
APPROX_WORDS = ("como", "unos", "mas o menos", "alrededor de", "casi", "uns", "cerca de", "quase")

WEEKDAYS = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6,
            "segunda": 0, "terca": 1, "quarta": 2, "quinta": 3, "sexta": 4}
MONTHS = {m: i + 1 for i, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                          "septiembre", "octubre", "noviembre", "diciembre"])}
MONTHS.update({m: i + 1 for i, m in enumerate(["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho",
                                               "agosto", "setembro", "outubro", "novembro", "dezembro"])})
