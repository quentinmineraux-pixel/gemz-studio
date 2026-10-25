"""Rédaction des fiches par Claude : un seul appel par produit, plafond de dépense mensuel.

L'IA ne rédige que les parties variables (aspect visuel, anecdote, lithothérapie, nom traduit, tags…),
dans les 4 langues à la fois, à partir de 2 ou 3 photos réduites. Les textes fixes sont dans texts.py.
"""
from __future__ import annotations

import base64
import io
import json
from datetime import date

from PIL import Image

from .. import vault
from ..config import ProductKind, Settings
from ..thumbs import sized_url
from ..models import ProductInfo
from .countries import country_name
from .texts import LANGS

# Prix en dollars par million de jetons (entrée, sortie)
MODELS = {
    "claude-opus-5-5": ("Claude Opus 5.5 (meilleure rédaction, ~0,08 € par fiche)", 4.00, 20.00),
    "claude-sonnet-5-5": ("Claude Sonnet 5.5 (~0,04 € par fiche)", 2.00, 10.00),
    "claude-haiku-5-5": ("Claude Haiku 5.5 (le moins cher, ~0,002 € par fiche)", 0.10, 0.50),
}
DEFAULT_MODEL = "claude-opus-5-5"
SECRET_KEY = "anthropic_api_key"
PHOTO_SIDE = 768

STYLE_EXAMPLE = """Exemple du style de la boutique (Vanadinite sur baryte, Mibladen, Maroc, 47 g) :
aspect : « <strong>Lames de baryte blanche en éventail</strong>, ornées de <strong>petits cristaux rouge-orangé de vanadinite</strong> brillants. Pièce compacte et sculpturale, au contraste très net. »
anecdote : « La vanadinite est un <strong>chlorovanadate de plomb</strong> qui cristallise en <strong>prismes hexagonaux</strong>. C'est l'un des principaux minerais de vanadium, et Mibladen est l'un des gisements les plus célèbres au monde pour ce minéral. »
lithotherapie : « Dans la tradition lithothérapeutique, la vanadinite est associée à l'énergie et à l'organisation, la baryte à la sérénité. »"""

SYSTEM = f"""Tu rédiges les fiches produits de GemZ, boutique française de minéraux, pierres polies et bijoux en pierres naturelles.
Tu reçois les données saisies par le vendeur et quelques photos de la pièce exacte vendue. Tu rédiges uniquement les parties variables de la fiche, en français (fr), anglais (en), espagnol (es) et allemand (de). L'outil ajoute lui-même le poids, les dimensions, la provenance, les titres de section et les mentions fixes : ne les répète pas.

Règles :
- aspect : 1 à 3 phrases sur ce que montrent les photos (couleurs, cristaux, éclat, inclusions, forme). Décris seulement ce qui est visible. Mets en gras avec <strong>…</strong> 1 ou 2 groupes de mots clés, aucune autre balise.
- anecdote : 2 ou 3 phrases factuelles de minéralogie (composition, système cristallin, gisement célèbre). Rien d'inventé : en cas de doute, reste général.
- lithotherapie : 1 ou 2 phrases présentées comme une tradition (« Dans la tradition lithothérapeutique… »), jamais comme un effet prouvé ni une promesse de soin.
- accroche : 1 ou 2 phrases de présentation, utilisées pour les bijoux et bracelets (laisse-la courte pour les minéraux).
- nom : le nom de la pierre ou du spécimen, sans le mot « bague » ni « bracelet », avec une majuscule (ex. « Vanadinite sur baryte »).
- particularite : courte précision qui suit le nom sans virgule (ex. « en éventail », « fibreuse »), ou vide.
- pays : le nom du pays dans la langue.
- seo_description : 140 à 160 caractères, avec le nom, la provenance, le poids et les dimensions s'ils sont donnés, et finissant par « Pièce unique. » ou son équivalent.
- alt : texte alternatif de la photo principale, 6 à 12 mots.
- etsy_tags : 13 tags de 20 caractères maximum, moitié français moitié anglais, sans doublon, en minuscules (ex. « vanadinite », « mineral specimen », « cristal rouge »).
- couleurs : 1 à 3 couleurs principales en français (ex. « rouge », « blanc »).
- pierres : les minéraux visibles ou indiqués, en français, en minuscules (ex. « vanadinite », « baryte »).
Ton : précis, élégant, sans superlatifs creux ni emojis. Ne mentionne jamais le prix.

{STYLE_EXAMPLE}"""

_LANG_SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "string"} for k in
                   ("nom", "particularite", "pays", "accroche", "aspect", "anecdote", "lithotherapie",
                    "seo_description", "alt")},
    "required": ["nom", "particularite", "pays", "accroche", "aspect", "anecdote", "lithotherapie",
                 "seo_description", "alt"],
    "additionalProperties": False,
}
SCHEMA = {
    "type": "object",
    "properties": {
        **{lang: _LANG_SCHEMA for lang in LANGS},
        "etsy_tags": {"type": "array", "items": {"type": "string"}},
        "couleurs": {"type": "array", "items": {"type": "string"}},
        "pierres": {"type": "array", "items": {"type": "string"}},
    },
    "required": list(LANGS) + ["etsy_tags", "couleurs", "pierres"],
    "additionalProperties": False,
}


class AiError(Exception):
    pass


class AiBudgetError(AiError):
    pass


def api_key() -> str:
    return vault.get(SECRET_KEY)


def month_key() -> str:
    return f"ia_cout_{date.today():%Y-%m}"


def month_cost(catalog) -> float:
    return float(catalog.kv_get(month_key(), "0") or 0)


def estimate_eur(settings: Settings, photos: int) -> float:
    """Coût prudent d'une fiche (avant l'appel), pour ne jamais dépasser le plafond."""
    _label, price_in, price_out = MODELS.get(settings.ai_model, MODELS[DEFAULT_MODEL])
    tokens_in = 2500 + photos * 900
    tokens_out = 6000  # textes des 4 langues + réflexion
    return (tokens_in * price_in + tokens_out * price_out) / 1_000_000 / settings.usd_per_eur


def pick_photos(photos: list[str], count: int) -> list[str]:
    """La principale, puis des vues réparties dans la série (les angles du plateau tournant)."""
    if len(photos) <= count:
        return list(photos)
    step = len(photos) / count
    return [photos[int(i * step)] for i in range(count)]


def _encode(source) -> dict:
    """Photo locale (chemin) ou téléchargée (octets), réduite à 768 px : peu de jetons par photo."""
    img = Image.open(io.BytesIO(source) if isinstance(source, bytes) else source)
    img.draft("RGB", (PHOTO_SIDE, PHOTO_SIDE))
    img = img.convert("RGB")
    img.thumbnail((PHOTO_SIDE, PHOTO_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(buf.getvalue()).decode("ascii")}}


def _download(url: str) -> bytes:
    import requests

    r = requests.get(sized_url(url, PHOTO_SIDE), timeout=60, headers={"User-Agent": "GemZ-Studio/1.0"})
    r.raise_for_status()
    return r.content


def product_facts(info: ProductInfo, kind: ProductKind) -> str:
    rows = [("Type de produit", kind.label), ("Nom saisi", info.nom), ("Variété", info.variete),
            ("Pays", country_name(info.pays, "fr") if info.pays else ""), ("Région / mine", info.region),
            ("Poids", f"{info.poids_g} g" if info.poids_g else ""), ("Dimensions", f"{info.dimensions} cm" if info.dimensions else ""),
            ("Traitement", info.traitement), ("Sous-catégories", ", ".join(info.sous_categories))]
    if kind.form == "bague":
        rows += [("Métal", info.metal), ("Taille réelle (FR)", info.taille_reelle),
                 ("Dimensions de la pierre", info.dimensions_pierre)]
    if kind.form == "bracelet":
        rows += [("Pierres", info.pierres), ("Perles", f"{info.perles_mm} mm" if info.perles_mm else ""),
                 ("Fil", info.fil)]
    return "\n".join(f"{label} : {value}" for label, value in rows if str(value).strip())


def generate(info: ProductInfo, kind: ProductKind, photos: list[str], settings: Settings, catalog,
             image_urls: list[str] | None = None, current_text: str = "") -> tuple[dict, float]:
    """Renvoie (textes par langue + tags, coût en euros). Lève AiError / AiBudgetError.

    Pièce importée de Shopify (pas de photo locale) : les photos de la boutique sont utilisées, et le
    texte actuel est fourni pour que la réécriture garde les faits.
    """
    key = api_key()
    if not key:
        raise AiError("Clé IA non renseignée (Réglages → IA) : la fiche reste à compléter.")
    chosen = pick_photos(list(photos) or list(image_urls or []), max(1, settings.ai_photos))
    spent = month_cost(catalog)
    if spent + estimate_eur(settings, len(chosen)) > settings.ai_monthly_cap_eur:
        raise AiBudgetError(f"Plafond IA du mois atteint ({spent:.2f} € sur {settings.ai_monthly_cap_eur:.2f} €).")

    import anthropic

    model = settings.ai_model if settings.ai_model in MODELS else DEFAULT_MODEL
    content = []
    for source in chosen:
        try:
            content.append(_encode(_download(source) if source.startswith("https://") else source))
        except Exception as e:  # noqa: BLE001 - une photo illisible ne bloque pas les autres
            failure = e
    if chosen and not content:
        raise AiError(f"Photos de la pièce introuvables, nouvel essai plus tard ({failure}).")
    text = "Données du produit :\n" + product_facts(info, kind)
    if not content:
        text += ("\n\nAucune photo fournie : pour « aspect », reste général et n'invente aucun détail visuel "
                 "(couleur, forme) qui ne figure pas dans les données.")
    if current_text.strip():
        text += ("\n\nTexte actuel de la fiche, à réécrire dans le format demandé en gardant ses faits "
                 "(n'en ajoute pas d'autres) :\n" + current_text.strip()[:3000])
    content.append({"type": "text", "text": text})
    request = dict(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
    )
    client = anthropic.Anthropic(api_key=key, max_retries=3, timeout=180)
    try:
        if model in ("claude-opus-5-5", "claude-sonnet-5-5"):
            # Si un filtre de sécurité refuse (rare ici), un autre modèle reprend dans le même appel
            response = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                                   fallbacks="default", **request)
        else:
            response = client.messages.create(**request)
    except anthropic.AuthenticationError as e:
        raise AiError("Clé IA refusée : vérifie-la dans Réglages → IA.") from e
    except anthropic.RateLimitError as e:
        raise AiError("Trop de demandes à l'IA pour l'instant, nouvel essai plus tard.") from e
    except anthropic.APIStatusError as e:
        raise AiError(f"Erreur du service IA ({e.status_code}).") from e
    except anthropic.APIConnectionError as e:
        raise AiError("Pas de connexion au service IA.") from e

    usage = response.usage
    served = MODELS.get(getattr(response, "model", model), MODELS[model])
    cost_usd = (usage.input_tokens * served[1] + usage.output_tokens * served[2]) / 1_000_000
    cost_eur = cost_usd / settings.usd_per_eur
    catalog.add_float(month_key(), cost_eur)

    if response.stop_reason == "refusal":
        raise AiError("L'IA a refusé de rédiger cette fiche : complète-la à la main.")
    if response.stop_reason == "max_tokens":
        raise AiError("Réponse de l'IA incomplète : réessaie.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except ValueError as e:
        raise AiError("Réponse de l'IA illisible : réessaie.") from e
    return data, cost_eur
