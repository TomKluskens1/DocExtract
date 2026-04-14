"""
Extractor factory – alleen native image processing.

Conform de technische afbakening van de bachelorproef: de PDF wordt
direct als afbeelding aangeleverd aan het multimodale LLM. Tekst-
gebaseerde extractors (pypdf, pymupdf-xml) zijn uitgesloten om
foutpropagatie vanuit een OCR/parse-stap te vermijden.
"""
from typing import List
from .base_extractor import BaseExtractor
from .image_extractor import ImageExtractor


def get_all_extractors() -> List[BaseExtractor]:
    """Geef alle beschikbare extractors terug.

    In de huidige configuratie is dit uitsluitend de ImageExtractor
    (native image processing via PyMuPDF → PNG → multimodaal LLM).
    """
    try:
        return [ImageExtractor(dpi=150)]
    except ImportError as e:
        print(f"Warning: ImageExtractor niet beschikbaar: {e}")
        return []


def get_extractor_by_name(name: str, **kwargs) -> BaseExtractor:
    """Geef extractor op naam terug.

    Enige geldige naam is 'PDF-Images' (en aliassen).

    Args:
        name: Naam van de extractor (hoofdletterongevoelig).
        **kwargs: Extra parameters (bijv. dpi=200).

    Raises:
        ValueError: Als de naam niet herkend wordt.
        ImportError: Als PyMuPDF niet geïnstalleerd is.
    """
    name_lower = name.lower().replace(" ", "-").replace("_", "-")

    image_aliases = {"pdf-images", "images", "image", "pdf-image"}

    if name_lower not in image_aliases:
        raise ValueError(
            f"Extractor '{name}' niet beschikbaar. "
            f"Gebruik één van: {', '.join(sorted(image_aliases))}"
        )

    return ImageExtractor(**kwargs)
