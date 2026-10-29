"""Client de l'API Early (v4) : tout ce qui parle à Early est ici, rien de MCP.

Pourquoi un fichier séparé ? Le serveur MCP (serveur.py) ne fait que décrire des
outils au LLM. La logique « comment parler à Early » vit ici : on peut la tester
seule, et la changer sans toucher aux outils.
"""

import os

import httpx

BASE_URL = "https://api.early.app/api/v4"


class EarlyError(Exception):
    """Erreur lisible venant d'Early (réseau, droits, ressource introuvable)."""


class EarlyClient:
    def __init__(self, api_key: str, api_secret: str, base_url: str = BASE_URL,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._api_key = api_key
        self._api_secret = api_secret
        # `transport` permet aux tests de remplacer le vrai réseau par de fausses réponses.
        self._http = httpx.AsyncClient(base_url=base_url, transport=transport, timeout=15)
        self._token: str | None = None  # jeton gardé en mémoire, jamais écrit sur disque

    @classmethod
    def depuis_env(cls) -> "EarlyClient":
        """Lit la clé et le secret dans les variables d'environnement (voir .env.example)."""
        cle, secret = os.environ.get("EARLY_API_KEY"), os.environ.get("EARLY_API_SECRET")
        if not cle or not secret:
            raise EarlyError("EARLY_API_KEY et EARLY_API_SECRET doivent être définis (fichier .env).")
        return cls(cle, secret)

    async def _se_connecter(self) -> str:
        rep = await self._http.post("/developer/sign-in",
                                    json={"apiKey": self._api_key, "apiSecret": self._api_secret})
        if rep.status_code != 200:
            raise EarlyError("Connexion à Early refusée : vérifiez EARLY_API_KEY / EARLY_API_SECRET.")
        self._token = rep.json()["token"]
        return self._token

    async def _requete(self, methode: str, chemin: str, **kw) -> httpx.Response:
        if self._token is None:
            await self._se_connecter()
        for tentative in (1, 2):
            rep = await self._http.request(methode, chemin,
                                           headers={"Authorization": f"Bearer {self._token}"}, **kw)
            if rep.status_code == 401 and tentative == 1:
                await self._se_connecter()  # jeton expiré : on en redemande un, une seule fois
                continue
            return rep
        raise AssertionError("inatteignable")

    async def _json(self, methode: str, chemin: str, **kw) -> dict:
        rep = await self._requete(methode, chemin, **kw)
        if rep.status_code >= 400:
            try:
                detail = rep.json()["error"]["message"]
            except Exception:
                detail = rep.text[:200]
            raise EarlyError(f"Early a répondu {rep.status_code} sur {chemin} : {detail}")
        return rep.json()

    # --- Lecture seule ---------------------------------------------------

    async def activites(self) -> dict:
        """{'activities': [...], 'inactiveActivities': [...], 'archivedActivities': [...]}"""
        return await self._json("GET", "/activities")

    async def tags_et_mentions(self) -> dict:
        return await self._json("GET", "/tags-and-mentions")

    async def suivi_en_cours(self) -> dict | None:
        rep = await self._requete("GET", "/tracking")
        if rep.status_code == 404:  # Early répond 404 quand rien n'est en cours
            return None
        if rep.status_code >= 400:
            raise EarlyError(f"Early a répondu {rep.status_code} sur /tracking")
        return rep.json()

    async def entrees(self, debut: str, fin: str) -> list[dict]:
        """Entrées de temps entre deux dates au format 2026-01-01T00:00:00.000"""
        return (await self._json("GET", f"/time-entries/{debut}/{fin}"))["timeEntries"]

    # --- Écriture --------------------------------------------------------

    async def creer_entree(self, activite_id: str, debut: str, fin: str, note: str | None) -> dict:
        corps = {"activityId": activite_id, "startedAt": debut, "stoppedAt": fin}
        if note:
            corps["note"] = {"text": note}
        return await self._json("POST", "/time-entries", json=corps)

    async def fermer(self) -> None:
        await self._http.aclose()
