"""Filtres de gabarit du jeu."""

from django import template
from django.utils.html import format_html

from esport.countries import country_code, country_label

register = template.Library()

#: Drapeaux servis par flagcdn.com (images libres de droits).
FLAG_URL = "https://flagcdn.com/{code}.svg"


@register.filter
def flag(country, size=18):
    """Le drapeau d'un pays Leaguepedia, ou son nom s'il est inconnu."""
    code = country_code(country)
    if not code:
        return country or ""
    label = country_label(country)
    return format_html(
        '<img class="flag" src="{}" alt="{}" title="{}" height="{}" loading="lazy">',
        FLAG_URL.format(code=code),
        label,
        label,
        size,
    )
