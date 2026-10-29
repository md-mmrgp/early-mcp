"""Protection du serveur quand il est exposé sur Internet (mode HTTP).

Sans ça, quiconque connaît l'URL pourrait lire (et écrire) dans le suivi du temps
avec VOTRE clé Early. Le client MCP doit envoyer `Authorization: Bearer <jeton>`.
"""

import hmac
import os

from fastmcp.server.auth import AccessToken, TokenVerifier

LONGUEUR_MIN = 32  # un jeton court se devine ; 32+ caractères aléatoires, non


class JetonUnique(TokenVerifier):
    """Accepte un seul jeton secret, lu dans l'environnement (jamais écrit dans le code)."""

    def __init__(self, jeton: str):
        super().__init__()
        self._jeton = jeton.encode()

    async def verify_token(self, token: str) -> AccessToken | None:
        # compare_digest : comparaison en temps constant, pour ne pas laisser fuiter
        # le jeton par de minuscules écarts de durée de réponse.
        if hmac.compare_digest(token.encode(), self._jeton):
            return AccessToken(token=token, client_id="utilisateur", scopes=[])
        return None


def verificateur_depuis_env() -> JetonUnique | None:
    """None si MCP_AUTH_TOKEN n'est pas défini (stdio local). Refuse un jeton trop court."""
    jeton = os.environ.get("MCP_AUTH_TOKEN")
    if not jeton:
        return None
    if len(jeton) < LONGUEUR_MIN:
        raise RuntimeError(f"MCP_AUTH_TOKEN trop court (minimum {LONGUEUR_MIN} caractères).")
    return JetonUnique(jeton)
