"""Rédaction IA : un seul appel, coût compté dans le mois, plafond respecté (client simulé)."""
from types import SimpleNamespace

import pytest

from gemz_studio.catalog import Catalog
from gemz_studio.config import Settings
from gemz_studio.listing import ai
from gemz_studio.listing.builder import build_listing
from gemz_studio.models import ProductInfo

ANSWER = {
    lang: {"nom": "vanadinite", "particularite": "sur baryte", "pays": "", "accroche": "Rouge vif.",
           "aspect": "Cristaux hexagonaux rouges.", "anecdote": "Contient du vanadium.",
           "lithotherapie": "Pierre d'énergie.", "seo_description": "Vanadinite du Maroc.", "alt": "vanadinite"}
    for lang in ("fr", "en", "es", "de")
}
ANSWER.update(etsy_tags=["vanadinite", "baryte"], couleurs=["rouge"], pierres=["vanadinite", "baryte"])


class FakeMessages:
    def __init__(self, calls):
        self.calls = calls

    def create(self, **kwargs):
        import json
        self.calls.append(kwargs)
        return SimpleNamespace(
            model=kwargs["model"], stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=4000, output_tokens=2000),
            content=[SimpleNamespace(type="text", text=json.dumps(ANSWER))])


@pytest.fixture
def env(tmp_path, monkeypatch):
    import anthropic
    monkeypatch.setenv("GEMZ_STUDIO_HOME", str(tmp_path))
    calls = []
    client = SimpleNamespace(messages=FakeMessages(calls), beta=SimpleNamespace(messages=FakeMessages(calls)))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: client)
    monkeypatch.setattr(ai, "api_key", lambda: "sk-test")
    catalog = Catalog(tmp_path / "cat.db")
    yield Settings(), catalog, calls
    catalog.close()


def test_generation_complete_la_fiche(env):
    settings, catalog, calls = env
    info = ProductInfo(nom="vanadinite sur baryte", pays="maroc", poids_g="47", prix="89")
    kind = settings.kind("mineral-collection")
    data, cost = ai.generate(info, kind, [], settings, catalog)
    assert len(calls) == 1 and calls[0]["model"] == "claude-opus-5-5"
    assert calls[0]["output_config"]["effort"] == "low"
    assert 0 < cost < 0.2 and abs(ai.month_cost(catalog) - cost) < 1e-6
    listing = build_listing(info, kind, settings, data, cost)
    assert listing.ready and listing.has_lang("en")
    assert "Cristaux hexagonaux" in listing.texts["fr"].description


def test_plafond_mensuel(env):
    settings, catalog, calls = env
    settings.ai_monthly_cap_eur = 0.01
    with pytest.raises(ai.AiBudgetError):
        ai.generate(ProductInfo(nom="quartz", pays="bresil", prix="10"), settings.kind("pierre-polie"), [],
                    settings, catalog)
    assert calls == []
