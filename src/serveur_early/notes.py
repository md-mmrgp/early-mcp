"""Les notes Early mélangent du texte libre et des balises : <{{|t|12|}}> = tag n°12, <{{|m|3|}}> = mention n°3.

Fonctions pures pour lire et reconstruire ces notes sans jamais laisser du texte libre fabriquer une balise.
"""

import re

BALISE_TAG = re.compile(r"<\{\{\|t\|(\d+)\|\}\}>")
BALISE_MENTION = re.compile(r"<\{\{\|m\|\d+\|\}\}>")
TOUTE_BALISE = re.compile(r"<\{\{\|[tm]\|\d+\|\}\}>")


def marqueur_tag(tag_id: int | str) -> str:
    return "<{{|t|" + str(int(tag_id)) + "|}}>"  # int() : un identifiant, jamais du texte libre


def texte_brut(texte: str) -> str:
    """Le texte sans aucune balise, espaces normalisés."""
    return " ".join(TOUTE_BALISE.sub(" ", texte or "").split())


def ids_tags(texte: str) -> list[int]:
    return [int(i) for i in BALISE_TAG.findall(texte or "")]


def marqueurs_mentions(texte: str) -> list[str]:
    return BALISE_MENTION.findall(texte or "")


def assembler(brut: str, tag_ids: list[int | str], mentions: list[str]) -> str:
    """Texte libre + balises de tags + balises de mentions déjà présentes (conservées telles quelles)."""
    return " ".join(filter(None, [brut.strip(), *[marqueur_tag(i) for i in tag_ids], *mentions]))
