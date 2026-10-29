"""Retrouver un élément Early (activité, tag client) à partir de son NOM.

Règle de sécurité : on ne devine jamais. Nom exact (à la casse et aux accents près)
ou erreur claire avec des suggestions. Facturer une heure au mauvais client est pire
qu'un refus.
"""

import difflib
import unicodedata


class ResolutionError(Exception):
    """Nom introuvable ou ambigu : le message est destiné au LLM (et donc à l'utilisateur)."""


def normaliser(texte: str) -> str:
    sans_accents = "".join(c for c in unicodedata.normalize("NFD", texte)
                           if unicodedata.category(c) != "Mn")
    return " ".join(sans_accents.casefold().split())


def trouver(nom: str, candidats: list[dict], cle_nom: str, genre: str) -> dict:
    """Renvoie l'élément dont le nom correspond exactement, sinon lève ResolutionError."""
    cible = normaliser(nom)
    exacts = [c for c in candidats if normaliser(c[cle_nom]) == cible]
    if len(exacts) == 1:
        return exacts[0]
    if len(exacts) > 1:
        noms = ", ".join(f"{c[cle_nom]!r} (id {c['id']})" for c in exacts)
        raise ResolutionError(f"Plusieurs {genre}s portent le nom {nom!r} : {noms}.")
    proches = difflib.get_close_matches(cible, [normaliser(c[cle_nom]) for c in candidats], n=3, cutoff=0.5)
    suggestions = [c[cle_nom] for c in candidats if normaliser(c[cle_nom]) in proches]
    conseil = f" Vouliez-vous dire : {', '.join(map(repr, suggestions))} ?" if suggestions else ""
    raise ResolutionError(f"Aucun {genre} nommé {nom!r}.{conseil}")
