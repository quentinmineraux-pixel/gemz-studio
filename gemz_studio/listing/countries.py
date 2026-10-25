"""Noms des pays dans les 4 langues des fiches (la saisie garde la forme courte sans accent)."""
from __future__ import annotations

from ..quickentry import strip_accents

# clé = forme saisie (minuscules, sans accent) : fr, en, es, de
COUNTRIES: dict[str, tuple[str, str, str, str]] = {
    "afghanistan": ("Afghanistan", "Afghanistan", "Afganistán", "Afghanistan"),
    "afrique du sud": ("Afrique du Sud", "South Africa", "Sudáfrica", "Südafrika"),
    "allemagne": ("Allemagne", "Germany", "Alemania", "Deutschland"),
    "angleterre": ("Angleterre", "England", "Inglaterra", "England"),
    "argentine": ("Argentine", "Argentina", "Argentina", "Argentinien"),
    "australie": ("Australie", "Australia", "Australia", "Australien"),
    "autriche": ("Autriche", "Austria", "Austria", "Österreich"),
    "bolivie": ("Bolivie", "Bolivia", "Bolivia", "Bolivien"),
    "bresil": ("Brésil", "Brazil", "Brasil", "Brasilien"),
    "bulgarie": ("Bulgarie", "Bulgaria", "Bulgaria", "Bulgarien"),
    "canada": ("Canada", "Canada", "Canadá", "Kanada"),
    "chili": ("Chili", "Chile", "Chile", "Chile"),
    "chine": ("Chine", "China", "China", "China"),
    "colombie": ("Colombie", "Colombia", "Colombia", "Kolumbien"),
    "congo": ("Congo", "Congo", "Congo", "Kongo"),
    "rdc": ("République démocratique du Congo", "Democratic Republic of the Congo",
            "República Democrática del Congo", "Demokratische Republik Kongo"),
    "egypte": ("Égypte", "Egypt", "Egipto", "Ägypten"),
    "espagne": ("Espagne", "Spain", "España", "Spanien"),
    "etats-unis": ("États-Unis", "United States", "Estados Unidos", "Vereinigte Staaten"),
    "usa": ("États-Unis", "United States", "Estados Unidos", "Vereinigte Staaten"),
    "ethiopie": ("Éthiopie", "Ethiopia", "Etiopía", "Äthiopien"),
    "france": ("France", "France", "Francia", "Frankreich"),
    "grece": ("Grèce", "Greece", "Grecia", "Griechenland"),
    "inde": ("Inde", "India", "India", "Indien"),
    "indonesie": ("Indonésie", "Indonesia", "Indonesia", "Indonesien"),
    "iran": ("Iran", "Iran", "Irán", "Iran"),
    "islande": ("Islande", "Iceland", "Islandia", "Island"),
    "italie": ("Italie", "Italy", "Italia", "Italien"),
    "japon": ("Japon", "Japan", "Japón", "Japan"),
    "kazakhstan": ("Kazakhstan", "Kazakhstan", "Kazajistán", "Kasachstan"),
    "kenya": ("Kenya", "Kenya", "Kenia", "Kenia"),
    "madagascar": ("Madagascar", "Madagascar", "Madagascar", "Madagaskar"),
    "maroc": ("Maroc", "Morocco", "Marruecos", "Marokko"),
    "mexique": ("Mexique", "Mexico", "México", "Mexiko"),
    "mongolie": ("Mongolie", "Mongolia", "Mongolia", "Mongolei"),
    "mozambique": ("Mozambique", "Mozambique", "Mozambique", "Mosambik"),
    "myanmar": ("Myanmar", "Myanmar", "Myanmar", "Myanmar"),
    "namibie": ("Namibie", "Namibia", "Namibia", "Namibia"),
    "norvege": ("Norvège", "Norway", "Noruega", "Norwegen"),
    "nouvelle-zelande": ("Nouvelle-Zélande", "New Zealand", "Nueva Zelanda", "Neuseeland"),
    "pakistan": ("Pakistan", "Pakistan", "Pakistán", "Pakistan"),
    "perou": ("Pérou", "Peru", "Perú", "Peru"),
    "pologne": ("Pologne", "Poland", "Polonia", "Polen"),
    "portugal": ("Portugal", "Portugal", "Portugal", "Portugal"),
    "republique tcheque": ("République tchèque", "Czech Republic", "República Checa", "Tschechien"),
    "roumanie": ("Roumanie", "Romania", "Rumanía", "Rumänien"),
    "russie": ("Russie", "Russia", "Rusia", "Russland"),
    "slovaquie": ("Slovaquie", "Slovakia", "Eslovaquia", "Slowakei"),
    "sri lanka": ("Sri Lanka", "Sri Lanka", "Sri Lanka", "Sri Lanka"),
    "suede": ("Suède", "Sweden", "Suecia", "Schweden"),
    "suisse": ("Suisse", "Switzerland", "Suiza", "Schweiz"),
    "tanzanie": ("Tanzanie", "Tanzania", "Tanzania", "Tansania"),
    "thailande": ("Thaïlande", "Thailand", "Tailandia", "Thailand"),
    "turquie": ("Turquie", "Turkey", "Turquía", "Türkei"),
    "ukraine": ("Ukraine", "Ukraine", "Ucrania", "Ukraine"),
    "uruguay": ("Uruguay", "Uruguay", "Uruguay", "Uruguay"),
    "vietnam": ("Vietnam", "Vietnam", "Vietnam", "Vietnam"),
    "zambie": ("Zambie", "Zambia", "Zambia", "Sambia"),
    "zimbabwe": ("Zimbabwe", "Zimbabwe", "Zimbabue", "Simbabwe"),
}
LANGS = ("fr", "en", "es", "de")


def country_name(pays: str, lang: str = "fr") -> str:
    """« bresil » -> « Brésil » (fr) / « Brazil » (en) ; un pays inconnu est rendu tel quel, avec majuscule."""
    key = strip_accents(pays.strip().lower())
    names = COUNTRIES.get(key)
    if names:
        return names[LANGS.index(lang)]
    text = pays.strip()
    return text[:1].upper() + text[1:]
