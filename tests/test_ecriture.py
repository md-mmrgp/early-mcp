"""Tests des outils d'écriture (tags, modification d'entrée) avec un FAUX Early qui enregistre ce qu'on lui envoie."""

import json
import re
from datetime import datetime

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from serveur_early import serveur
from serveur_early.early_client import EarlyClient

ACTIVITES = {"activities": [{"id": "1", "name": "Conseil", "folderId": "9"}, {"id": "3", "name": "Admin", "folderId": "9"}],
             "inactiveActivities": [], "archivedActivities": [{"id": "2", "name": "Ancien", "folderId": "9"}]}
DOSSIER = {"id": "9", "name": "ERIA", "status": "active", "members": [{"id": "1", "name": "X", "email": "x@example.com"}]}


class FauxEarly:
    def __init__(self, dossiers=None):
        self.tags = [{"id": 7, "label": "Dupont"}, {"id": 8, "label": "Martin"}, {"id": 5, "label": "nomad RP"}]
        self.dossiers = dossiers or [DOSSIER]
        self.entrees = {
            "1": {"id": "1", "activity": {"id": "1", "name": "Conseil"},
                  "duration": {"startedAt": "2026-01-05T09:00:00.000", "stoppedAt": "2026-01-05T12:00:00.000"},
                  "note": {"text": "Atelier <{{|t|7|}}> <{{|m|3|}}>", "tags": [{"id": 7, "label": "Dupont"}],
                           "mentions": [{"id": 3, "label": "JIRA-1"}]}},
            "2": {"id": "2", "activity": {"id": "3", "name": "Admin"},
                  "duration": {"startedAt": "2026-01-05T13:00:00.000", "stoppedAt": "2026-01-05T14:00:00.000"}, "note": None},
        }
        self.ecritures: list[tuple[str, str, dict]] = []

    def _note(self, texte):
        ids = [int(i) for i in re.findall(r"<\{\{\|t\|(\d+)\|\}\}>", texte)]
        return {"text": texte, "tags": [t for t in self.tags if t["id"] in ids], "mentions": []}

    def transport(self) -> httpx.MockTransport:
        def repondre(req: httpx.Request) -> httpx.Response:
            chemin, m = req.url.path, req.method
            corps = json.loads(req.content) if req.content else {}
            if chemin.endswith("/developer/sign-in"):
                return httpx.Response(200, json={"token": "t"})
            if m == "GET":
                if chemin.endswith("/activities"):
                    return httpx.Response(200, json=ACTIVITES)
                if chemin.endswith("/tags-and-mentions"):
                    return httpx.Response(200, json={"tags": self.tags, "mentions": [{"id": 3, "label": "JIRA-1"}]})
                if chemin.endswith("/folders"):
                    return httpx.Response(200, json={"folders": self.dossiers})
                if mo := re.search(r"/time-entries/(\d+)$", chemin):
                    e = self.entrees.get(mo.group(1))
                    return httpx.Response(200, json=e) if e else httpx.Response(404, json={"error": {"message": "not found"}})
                if mo := re.search(r"/time-entries/([^/]+)/([^/]+)$", chemin):
                    d, f = datetime.fromisoformat(mo.group(1)), datetime.fromisoformat(mo.group(2))
                    dedans = [e for e in self.entrees.values()
                              if datetime.fromisoformat(e["duration"]["startedAt"]) < f
                              and datetime.fromisoformat(e["duration"]["stoppedAt"]) > d]
                    return httpx.Response(200, json={"timeEntries": dedans})
            if m == "POST" and chemin.endswith("/tags"):
                self.ecritures.append((m, chemin, corps))
                return httpx.Response(200, json={"id": 99, **corps})
            if m == "PATCH" and (mo := re.search(r"/tags/(\d+)$", chemin)):
                self.ecritures.append((m, chemin, corps))
                return httpx.Response(200, json={"id": int(mo.group(1)), **corps})
            if m == "PATCH" and (mo := re.search(r"/time-entries/(\d+)$", chemin)):
                self.ecritures.append((m, chemin, corps))
                e = json.loads(json.dumps(self.entrees[mo.group(1)]))
                if "activityId" in corps:
                    e["activity"] = next(a for a in ACTIVITES["activities"] if a["id"] == corps["activityId"])
                if "startedAt" in corps:
                    e["duration"] = {"startedAt": corps["startedAt"], "stoppedAt": corps["stoppedAt"]}
                if "note" in corps:
                    e["note"] = self._note(corps["note"]["text"]) if corps["note"] else None
                return httpx.Response(200, json=e)
            return httpx.Response(404, json={"error": {"message": "route inconnue"}})
        return httpx.MockTransport(repondre)


@pytest.fixture
async def faux(monkeypatch):
    monkeypatch.delenv("MCP_AUTORISER_ECRASEMENT", raising=False)
    f = FauxEarly()
    monkeypatch.setattr(serveur, "_client", EarlyClient("k", "s", transport=f.transport()))
    async with Client(serveur.mcp) as c:
        f.client = c
        yield f


async def appel(f, outil, args, attendu_erreur=None):
    if attendu_erreur:
        with pytest.raises(ToolError, match=attendu_erreur):
            await f.client.call_tool(outil, args)
        return None
    return (await f.client.call_tool(outil, args)).data


# --- lister_entrees ---------------------------------------------------------------------------

async def test_lister_entrees_donne_les_ids_et_une_note_propre(faux):
    res = await appel(faux, "lister_entrees", {"date_debut": "2026-01-05"})
    assert [e["id"] for e in res["entrees"]] == ["1", "2"]
    assert res["entrees"][0] == {"id": "1", "activite": "Conseil", "de": "2026-01-05T09:00:00.000",
                                 "a": "2026-01-05T12:00:00.000", "clients": ["Dupont"], "note": "Atelier"}
    assert res["entrees"][1]["note"] is None and res["tronque"] is False


# --- creer_tag --------------------------------------------------------------------------------

async def test_creer_tag_ecrit_dans_le_bon_dossier(faux):
    res = await appel(faux, "creer_tag", {"nom": "  Nouveau   Client "})
    assert res["cree"]["nom"] == "Nouveau Client" and res["cree"]["dossier"] == "ERIA"
    methode, chemin, corps = faux.ecritures[0]
    assert (methode, corps["label"], corps["folderId"], corps["scope"]) == ("POST", "Nouveau Client", "9", "timeular")
    assert len(corps["key"]) >= 32


async def test_creer_tag_simulation_n_ecrit_rien(faux):
    res = await appel(faux, "creer_tag", {"nom": "Nouveau", "simuler": True})
    assert res["simulation"] is True and faux.ecritures == []


@pytest.mark.parametrize("nom", ["dupont", "Nomadrp", "nomad-rp", "NOMAD  RP"])
async def test_creer_tag_refuse_les_quasi_doublons(faux, nom):
    await appel(faux, "creer_tag", {"nom": nom}, attendu_erreur="existe déjà")
    assert faux.ecritures == []


@pytest.mark.parametrize("nom", ["", "   ", "x" * 61, "a<b", "a{{b", "#client", "a|b"])
async def test_creer_tag_refuse_les_noms_invalides(faux, nom):
    await appel(faux, "creer_tag", {"nom": nom}, attendu_erreur="nom d'un tag")
    assert faux.ecritures == []


async def test_creer_tag_plusieurs_dossiers_exige_un_choix(monkeypatch):
    f = FauxEarly(dossiers=[DOSSIER, {"id": "10", "name": "Perso", "status": "active", "members": []}])
    monkeypatch.setattr(serveur, "_client", EarlyClient("k", "s", transport=f.transport()))
    async with Client(serveur.mcp) as c:
        f.client = c
        await appel(f, "creer_tag", {"nom": "Nouveau"}, attendu_erreur="précisez le paramètre dossier")
        res = await appel(f, "creer_tag", {"nom": "Nouveau", "dossier": "perso"})
        assert res["cree"]["dossier"] == "Perso" and f.ecritures[0][2]["folderId"] == "10"


# --- renommer_tag -----------------------------------------------------------------------------

async def test_renommer_tag(faux):
    res = await appel(faux, "renommer_tag", {"tag": "nomad rp", "nouveau_nom": "NomadRP"})
    assert res["renomme"] == {"de": "nomad RP", "en": "NomadRP"}
    assert faux.ecritures == [("PATCH", "/api/v4/tags/5", {"label": "NomadRP"})]


async def test_renommer_tag_simulation(faux):
    res = await appel(faux, "renommer_tag", {"tag": "Martin", "nouveau_nom": "Martin SA", "simuler": True})
    assert res["simulation"] is True and faux.ecritures == []


async def test_renommer_tag_refuse_un_doublon(faux):
    await appel(faux, "renommer_tag", {"tag": "Martin", "nouveau_nom": "dupont"}, attendu_erreur="ferait doublon")
    # le message explique quoi faire quand l'ancien tag est inutilisé, et avertit s'il porte encore des entrées
    await appel(faux, "renommer_tag", {"tag": "Martin", "nouveau_nom": "dupont"}, attendu_erreur="supprimez-le directement dans Early")
    await appel(faux, "renommer_tag", {"tag": "Martin", "nouveau_nom": "dupont"}, attendu_erreur="ne le supprimez pas avant")
    assert faux.ecritures == []


async def test_renommer_tag_inconnu_propose_une_suggestion(faux):
    await appel(faux, "renommer_tag", {"tag": "Dupon", "nouveau_nom": "X"}, attendu_erreur="Dupont")
    await appel(faux, "renommer_tag", {"tag": "Martin", "nouveau_nom": "Martin"}, attendu_erreur="déjà")
    assert faux.ecritures == []


# --- modifier_entree_temps --------------------------------------------------------------------

async def test_modifier_activite_seule_n_envoie_que_l_activite(faux):
    res = await appel(faux, "modifier_entree_temps", {"entree_id": "1", "activite": "admin"})
    assert faux.ecritures == [("PATCH", "/api/v4/time-entries/1", {"activityId": "3"})]
    assert res["avant"]["activite"] == "Conseil" and res["apres"]["activite"] == "Admin"


async def test_remplacer_le_client_conserve_texte_et_mentions(faux):
    res = await appel(faux, "modifier_entree_temps", {"entree_id": "1", "clients": ["Martin"]})
    assert faux.ecritures[0][2] == {"note": {"text": "Atelier <{{|t|8|}}> <{{|m|3|}}>"}}
    assert res["avant"]["clients"] == ["Dupont"] and res["apres"]["clients"] == ["Martin"]


async def test_retirer_tous_les_clients(faux):
    await appel(faux, "modifier_entree_temps", {"entree_id": "1", "clients": []})
    assert faux.ecritures[0][2] == {"note": {"text": "Atelier <{{|m|3|}}>"}}


async def test_nouvelle_note_conserve_tags_et_mentions(faux):
    await appel(faux, "modifier_entree_temps", {"entree_id": "1", "note": "  Nouveau   texte "})
    assert faux.ecritures[0][2] == {"note": {"text": "Nouveau texte <{{|t|7|}}> <{{|m|3|}}>"}}


async def test_deplacer_dans_un_creneau_libre(faux):
    await appel(faux, "modifier_entree_temps", {"entree_id": "1", "debut": "2026-01-05T15:00", "fin": "2026-01-05T16:00"})
    assert faux.ecritures[0][2] == {"startedAt": "2026-01-05T15:00:00.000", "stoppedAt": "2026-01-05T16:00:00.000"}


async def test_se_chevaucher_soi_meme_n_est_pas_un_conflit(faux):
    await appel(faux, "modifier_entree_temps", {"entree_id": "1", "debut": "2026-01-05T09:30", "fin": "2026-01-05T11:30"})
    assert len(faux.ecritures) == 1


async def test_chevaucher_une_autre_entree_est_refuse(faux):
    await appel(faux, "modifier_entree_temps", {"entree_id": "1", "debut": "2026-01-05T12:30", "fin": "2026-01-05T13:30"},
                attendu_erreur="chevauche d'autres entrées")
    assert faux.ecritures == []


async def test_ecrasement_exige_la_variable_du_serveur(faux, monkeypatch):
    args = {"entree_id": "1", "debut": "2026-01-05T12:30", "fin": "2026-01-05T13:30", "ecraser_chevauchement": True}
    await appel(faux, "modifier_entree_temps", args, attendu_erreur="désactivé")
    assert faux.ecritures == []
    monkeypatch.setenv("MCP_AUTORISER_ECRASEMENT", "1")
    await appel(faux, "modifier_entree_temps", args)
    assert len(faux.ecritures) == 1


async def test_simulation_montre_avant_apres_sans_ecrire(faux):
    res = await appel(faux, "modifier_entree_temps", {"entree_id": "1", "clients": ["Martin"], "note": "Neuf", "simuler": True})
    assert res["simulation"] is True and faux.ecritures == []
    assert res["avant"]["clients"] == ["Dupont"] and res["avant"]["note"] == "Atelier"
    assert res["apres"]["clients"] == ["Martin"] and res["apres"]["note"] == "Neuf"


@pytest.mark.parametrize("identifiant", ["abc", "", "1/../tags", "1?x=2", "1 ", "../../me", "1234567890123"])
async def test_identifiant_non_numerique_refuse(faux, identifiant):
    await appel(faux, "modifier_entree_temps", {"entree_id": identifiant, "note": "x"}, attendu_erreur="identifiant numérique")
    assert faux.ecritures == []


@pytest.mark.parametrize("args,message", [
    ({"entree_id": "1"}, "Rien à modifier"),
    ({"entree_id": "1", "note": "ok <{{|t|8|}}>"}, "balise"),
    ({"entree_id": "1", "debut": "2026-01-05T12:00"}, "au moins 1 minute"),
    ({"entree_id": "1", "fin": "2026-01-06T12:00"}, "16 h"),
    ({"entree_id": "1", "debut": "hier"}, "format attendu"),
    ({"entree_id": "1", "activite": "Ancien"}, "Aucun activité"),
    ({"entree_id": "1", "clients": ["Dupon"]}, "Aucun tag client"),
    ({"entree_id": "999", "note": "x"}, "404"),
])
async def test_refus_sans_rien_ecrire(faux, args, message):
    await appel(faux, "modifier_entree_temps", args, attendu_erreur=message)
    assert faux.ecritures == []
