"""Textes fixes des fiches, traduits une fois pour toutes (aucun appel IA pour eux)."""
from __future__ import annotations

LANGS = ("fr", "en", "es", "de")
LANG_LABELS = {"fr": "Français", "en": "English", "es": "Español", "de": "Deutsch"}
LANG_NAMES = {"fr": "français", "en": "anglais", "es": "espagnol", "de": "allemand"}   # dans les messages

FIXED: dict[str, dict[str, str]] = {
    "poids": {"fr": "Poids", "en": "Weight", "es": "Peso", "de": "Gewicht"},
    "dimensions": {"fr": "Dimensions", "en": "Dimensions", "es": "Dimensiones", "de": "Maße"},
    "provenance": {"fr": "Provenance", "en": "Origin", "es": "Procedencia", "de": "Herkunft"},
    "mineraux": {"fr": "Minéraux", "en": "Minerals", "es": "Minerales", "de": "Mineralien"},
    "traitement": {"fr": "Traitement", "en": "Treatment", "es": "Tratamiento", "de": "Behandlung"},
    "aspect": {"fr": "Aspect visuel", "en": "Appearance", "es": "Aspecto visual", "de": "Aussehen"},
    "anecdote": {"fr": "Anecdote scientifique", "en": "Scientific note", "es": "Nota científica",
                 "de": "Wissenschaftliche Anmerkung"},
    "litho": {"fr": "Lithothérapie", "en": "Crystal lore", "es": "Litoterapia", "de": "Steinheilkunde"},
    "bienetre": {
        "fr": "Approche de bien-être non médicale : la lithothérapie ne remplace pas un avis médical.",
        "en": "Non-medical wellbeing approach: crystal lore is no substitute for medical advice.",
        "es": "Enfoque de bienestar no médico: la litoterapia no sustituye el consejo médico.",
        "de": "Nicht-medizinischer Wohlfühlansatz: Steinheilkunde ersetzt keinen ärztlichen Rat.",
    },
    "photo": {
        "fr": "Photos réalisées au Canon 6D Mark II avec un objectif macro Sigma 105 mm f/2.8, pour une "
              "restitution fidèle des couleurs et des textures. Vous recevrez exactement la pièce photographiée.",
        "en": "Photographed with a Canon 6D Mark II and a Sigma 105 mm f/2.8 macro lens for faithful colours "
              "and textures. You will receive the exact piece shown in the photos.",
        "es": "Fotos tomadas con una Canon 6D Mark II y un objetivo macro Sigma 105 mm f/2.8, para reproducir "
              "fielmente los colores y las texturas. Recibirás exactamente la pieza fotografiada.",
        "de": "Fotografiert mit einer Canon 6D Mark II und einem Sigma 105 mm f/2.8 Makroobjektiv für "
              "naturgetreue Farben und Strukturen. Sie erhalten genau das abgebildete Stück.",
    },
    "unique": {"fr": "Pièce unique.", "en": "One-of-a-kind piece.", "es": "Pieza única.", "de": "Unikat."},
    "taille": {"fr": "Taille", "en": "Size", "es": "Talla", "de": "Größe"},
    # Bagues
    "metal": {"fr": "Métal", "en": "Metal", "es": "Metal", "de": "Metall"},
    "dims_pierre": {"fr": "Dimensions de la pierre", "en": "Stone size", "es": "Tamaño de la piedra",
                    "de": "Steingröße"},
    "poids_total": {"fr": "Poids total", "en": "Total weight", "es": "Peso total", "de": "Gesamtgewicht"},
    "taille_reelle": {"fr": "Taille réelle", "en": "Actual size", "es": "Talla real", "de": "Tatsächliche Größe"},
    "bague": {"fr": "Bague", "en": "Ring", "es": "Anillo", "de": "Ring"},
    "mise_a_taille": {
        "fr": "Taille {taille} prête à expédier. Mise à taille possible de 40 à 70 (FR) pour {supplement}, "
              "avec un délai supplémentaire de {jours} jours ouvrés.",
        "en": "Size {taille} (FR) ready to ship. Resizing available from FR 40 to 70 for {supplement}, "
              "with an extra {jours} business days.",
        "es": "Talla {taille} (FR) lista para enviar. Ajuste posible de la talla 40 a la 70 (FR) por {supplement}, "
              "con un plazo adicional de {jours} días laborables.",
        "de": "Größe {taille} (FR) sofort versandbereit. Anpassung von FR 40 bis 70 für {supplement} möglich, "
              "zuzüglich {jours} Werktage.",
    },
    "guide_tailles": {"fr": "Guide des tailles", "en": "Size guide", "es": "Guía de tallas", "de": "Größentabelle"},
    # Bracelets
    "taille_bracelet": {"fr": "Taille du bracelet", "en": "Bracelet size", "es": "Talla de la pulsera",
                        "de": "Armbandgröße"},
    "tailles_dispo": {"fr": "Tours de poignet disponibles : {tailles}.",
                      "en": "Available wrist sizes: {tailles}.",
                      "es": "Contornos de muñeca disponibles: {tailles}.",
                      "de": "Verfügbare Handgelenkumfänge: {tailles}."},
    "caracteristiques": {"fr": "Caractéristiques techniques", "en": "Specifications", "es": "Características",
                         "de": "Technische Daten"},
    "pierre": {"fr": "Pierre", "en": "Stone", "es": "Piedra", "de": "Stein"},
    "perles": {"fr": "Diamètre des perles", "en": "Bead size", "es": "Diámetro de las cuentas",
               "de": "Perlendurchmesser"},
    "montage": {"fr": "Montage", "en": "Stringing", "es": "Montaje", "de": "Fädelung"},
    "particularite": {"fr": "Particularité", "en": "Good to know", "es": "Particularidad", "de": "Besonderheit"},
    "naturel_bracelet": {
        "fr": "Pierre naturelle : chaque bracelet présente des variations de couleur, de forme et de motif "
              "qui en font une pièce unique.",
        "en": "Natural stone: each bracelet shows its own variations in colour, shape and pattern, making it unique.",
        "es": "Piedra natural: cada pulsera presenta variaciones de color, forma y dibujo que la hacen única.",
        "de": "Naturstein: Jedes Armband zeigt eigene Farb-, Form- und Musterabweichungen und ist damit ein Unikat.",
    },
}

TREATMENTS: dict[str, dict[str, str]] = {
    "naturel": {"fr": "naturel, non traité", "en": "natural, untreated", "es": "natural, sin tratar",
                "de": "natürlich, unbehandelt"},
    "poli": {"fr": "poli", "en": "polished", "es": "pulido", "de": "poliert"},
    "chauffé": {"fr": "chauffé", "en": "heat-treated", "es": "calentado", "de": "erhitzt"},
    "teinté": {"fr": "teinté", "en": "dyed", "es": "teñido", "de": "gefärbt"},
    "stabilisé": {"fr": "stabilisé", "en": "stabilised", "es": "estabilizado", "de": "stabilisiert"},
}

METALS: dict[str, dict[str, str]] = {
    "argent 925": {"fr": "argent 925", "en": "925 sterling silver", "es": "plata 925", "de": "925er Silber"},
    "laiton": {"fr": "laiton", "en": "brass", "es": "latón", "de": "Messing"},
    "plaqué or": {"fr": "plaqué or", "en": "gold-plated", "es": "chapado en oro", "de": "vergoldet"},
    "acier inoxydable": {"fr": "acier inoxydable", "en": "stainless steel", "es": "acero inoxidable",
                         "de": "Edelstahl"},
    "cuivre": {"fr": "cuivre", "en": "copper", "es": "cobre", "de": "Kupfer"},
}

# Tailles de bague FR (tour de doigt en mm) -> US, pour le guide de la description
RING_SIZES_US = {44: "3", 47: "4", 49: "5", 52: "6", 54: "7", 57: "8", 59: "9", 62: "10", 65: "11", 67: "12"}


def t(key: str, lang: str, **values) -> str:
    text = FIXED[key][lang]
    return text.format(**values) if values else text


def translated(table: dict[str, dict[str, str]], value: str, lang: str) -> str:
    entry = table.get(value.strip().lower())
    return entry[lang] if entry else value
