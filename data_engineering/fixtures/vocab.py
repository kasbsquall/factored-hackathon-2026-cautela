"""Spanish vocabulary for the synthetic test fixture.

Every value here is team-invented test data. None of it is taken from the organizer dataset, which has not
arrived yet. Category and reason labels are plausible Spanish wording chosen so that dispute scenarios can be
exercised; the real labels will be profiled from the organizer data and may differ.
"""

COUNTRIES = {
    "Mexico": {
        "currency": "MXN",
        "accent": "mexican",
        "documents": ["CURP", "Passport"],
        "phone_prefix": "+52",
        "cities": [("Ciudad de México", "CDMX"), ("Guadalajara", "Jalisco"), ("Monterrey", "Nuevo León"),
                   ("Puebla", "Puebla"), ("Mérida", "Yucatán")],
    },
    "Colombia": {
        "currency": "COP",
        "accent": "colombian",
        "documents": ["CC", "CE"],
        "phone_prefix": "+57",
        "cities": [("Bogotá", "Cundinamarca"), ("Medellín", "Antioquia"), ("Cali", "Valle del Cauca"),
                   ("Barranquilla", "Atlántico"), ("Bucaramanga", "Santander")],
    },
    "Argentina": {
        "currency": "ARS",
        "accent": "argentine",
        "documents": ["DNI", "Passport"],
        "phone_prefix": "+54",
        "cities": [("Buenos Aires", "Buenos Aires"), ("Córdoba", "Córdoba"), ("Rosario", "Santa Fe"),
                   ("Mendoza", "Mendoza"), ("La Plata", "Buenos Aires")],
    },
}

# Local currency units per USD, used to build a plausible random walk for daily_exchange_rates.
USD_PER_UNIT = {"MXN": 0.058, "COP": 0.00025, "ARS": 0.0011}

FIRST_NAMES_F = ["María", "Guadalupe", "Sofía", "Valentina", "Camila", "Daniela", "Lucía", "Mariana",
                 "Gabriela", "Ana", "Paula", "Martina", "Florencia", "Juliana", "Ximena", "Fernanda"]
FIRST_NAMES_M = ["José", "Juan", "Luis", "Carlos", "Santiago", "Mateo", "Diego", "Andrés", "Miguel",
                 "Javier", "Sebastián", "Nicolás", "Tomás", "Alejandro", "Felipe", "Joaquín"]
LAST_NAMES = ["García", "Rodríguez", "Martínez", "Hernández", "López", "González", "Pérez", "Sánchez",
              "Ramírez", "Torres", "Flores", "Rivera", "Gómez", "Díaz", "Morales", "Castro", "Ortiz",
              "Vargas", "Romero", "Suárez", "Álvarez", "Jiménez", "Mendoza", "Aguilar", "Rojas", "Fernández"]
STREETS = ["Av. Insurgentes", "Calle 72", "Av. Corrientes", "Carrera 7", "Calle Reforma", "Av. Santa Fe",
           "Calle 10 de Mayo", "Av. Boyacá", "Calle San Martín", "Av. Juárez"]
OCCUPATIONS = ["Docente", "Comerciante", "Ingeniera", "Contador", "Enfermera", "Abogado", "Estudiante",
               "Médica", "Diseñador", "Empleado administrativo", "Chofer", "Independiente"]
MARITAL = ["Soltero", "Casado", "Unión libre", "Divorciado", "Viudo"]
EDUCATION = ["Secundaria", "Técnico", "Universitario", "Posgrado"]

PRODUCT_TYPES = ["Checking Account", "Savings Account", "Credit Card", "Debit Card", "Personal Loan",
                 "Mortgage", "Investment"]
CARD_PRODUCTS = ("Credit Card", "Debit Card")

MERCHANTS = {
    "Food": [("Supermercado La Estrella", "5411"), ("Restaurante El Fogón", "5812"), ("Panadería San José", "5462")],
    "Transport": [("Gasolinera Ruta 5", "5541"), ("Viajes Andinos", "4722"), ("Taxi Seguro", "4121")],
    "Services": [("Telefonía Conecta", "4814"), ("Energía del Valle", "4900"), ("Streaming Plus", "4899")],
    "Entertainment": [("Cine Plaza Mayor", "7832"), ("Boletería Total", "7922"), ("Juegos Online Max", "5816")],
    "Health": [("Farmacia San Rafael", "5912"), ("Clínica Santa Fe", "8062"), ("Óptica Visión", "8043")],
    "Other": [("Tienda Departamental Sol", "5311"), ("Ferretería El Clavo", "5251"), ("Marketplace Uno", "5399")],
}

CONTACT_REASONS = [
    ("Cargo no reconocido", "Transactional"), ("Aclaración de cobro", "Transactional"),
    ("Consulta de saldo", "Transactional"), ("Bloqueo de tarjeta", "Product"),
    ("Solicitud de crédito", "Commercial"), ("Problemas con la app", "Technical"),
    ("Cambio de datos personales", "Product"), ("Reclamo por comisión", "Complaint"),
]

# Complaint categories used by the fixture. The first four are dispute-related and are linked to a real
# fixture transaction (ground truth recorded in manifest.json under dispute_links).
DISPUTE_CATEGORIES = [
    ("Cargo no reconocido", "Compra con tarjeta"),
    ("Cobro duplicado", "Compra con tarjeta"),
    ("Fraude con tarjeta", "Uso no autorizado"),
    ("Compra no recibida", "Comercio no entregó"),
]
OTHER_CATEGORIES = [
    ("Cobro de comisiones", "Comisión de manejo"), ("Atención al cliente", "Mala atención"),
    ("Fallas en la app", "Error al ingresar"), ("Demora en transferencia", "Transferencia no acreditada"),
]

RESOLUTIONS = {
    "Resolved": "Se validó el caso y se realizó el ajuste correspondiente al cliente.",
    "Closed": "Caso cerrado luego de confirmar la información con el cliente.",
    "Rejected": "No procede el reclamo: la operación fue autenticada con los datos del titular.",
}

ACCENT_OPENERS = {
    "mexican": "Oiga, fíjese que",
    "colombian": "Buenas, qué pena con usted, es que",
    "argentine": "Hola, mirá, te cuento que",
}

SURVEY_QUESTIONS = ["¿Qué tan fácil fue resolver su solicitud?", "¿El asesor entendió su problema?",
                    "¿Recomendaría nuestro canal de atención?"]
SURVEY_COMMENTS = ["Muy buena atención, resolvieron rápido.", "Tuve que esperar mucho en la línea.",
                   "El asesor fue amable pero no solucionó el problema.", "Todo excelente, gracias."]

CAMPAIGN_NAMES = ["Tarjeta Oro sin anualidad", "Crédito personal express", "Ahorro programado",
                  "Seguro de vida familiar", "App nueva versión", "Reactiva tu cuenta"]
