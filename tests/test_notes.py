import pytest

from serveur_early import notes
from serveur_early.resolution import compacter, homonymes


def test_texte_brut_retire_toutes_les_balises():
    assert notes.texte_brut("Atelier <{{|t|7|}}>  <{{|m|3|}}> cadrage") == "Atelier cadrage"
    assert notes.texte_brut("") == "" and notes.texte_brut(None) == ""


def test_ids_et_mentions():
    t = "x <{{|t|7|}}> <{{|t|8|}}> <{{|m|3|}}>"
    assert notes.ids_tags(t) == [7, 8] and notes.marqueurs_mentions(t) == ["<{{|m|3|}}>"]


def test_assembler_conserve_les_mentions():
    assert notes.assembler("Atelier", [7, 9], ["<{{|m|3|}}>"]) == "Atelier <{{|t|7|}}> <{{|t|9|}}> <{{|m|3|}}>"
    assert notes.assembler("", [], []) == ""


def test_marqueur_tag_refuse_un_texte():
    assert notes.marqueur_tag("12") == "<{{|t|12|}}>"
    with pytest.raises(ValueError):
        notes.marqueur_tag("12|}}><{{|t|99")


def test_quasi_doublons():
    assert compacter("Nomad RP") == compacter("nomadrp") == compacter("nomad-rp")
    tags = [{"id": 1, "label": "nomad RP"}, {"id": 2, "label": "Dupont"}]
    assert [t["id"] for t in homonymes("nomadrp", tags, "label")] == [1]
    assert homonymes("nomadrp", tags, "label", ignorer_id=1) == []


async def test_le_client_refuse_lui_aussi_un_identifiant_piege():
    import httpx

    from serveur_early.early_client import EarlyClient, EarlyError

    appels = []
    c = EarlyClient("k", "s", transport=httpx.MockTransport(lambda r: appels.append(r) or httpx.Response(200, json={"token": "t"})))
    for mauvais in ["1/../me", "1?x", "abc", "", "١٢٣", "1" * 13, "-1"]:
        for appel in (c.entree(mauvais), c.modifier_entree(mauvais, {}), c.renommer_tag(mauvais, "x")):
            with pytest.raises(EarlyError, match="Identifiant invalide"):
                await appel
    assert appels == []  # aucune requête n'est partie
