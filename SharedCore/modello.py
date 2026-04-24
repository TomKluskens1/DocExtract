"""Pydantic model voor gestructureerde factuurextractie (BachelorProef Turtle Srl)."""
from typing import List, Optional
from pydantic import BaseModel


CO2_INTENSITY_G_PER_KWH = 200.5  # Meest recente coëfficiënt (2024)


def compute_co2eq(kwh_quantity) -> float | None:
    """Bereken co2eq_quantity in kg CO₂ op basis van kWh × CO2_INTENSITY_G_PER_KWH."""
    if kwh_quantity is None:
        return None
    try:
        return round(float(kwh_quantity) * CO2_INTENSITY_G_PER_KWH / 1000, 3)
    except (TypeError, ValueError):
        return None


class Periode(BaseModel):
    supplier: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    kwh_quantity: Optional[float] = None


class BachelorProefModel(BaseModel):
    """You are an expert at extracting structured data from Italian utility bills (bollette).
    Extract the following fields and return them as JSON.

    CRITICAL RULES — read carefully:
    1. Only extract the INVOICED billing period — the period this specific invoice charges for.
    2. IGNORE all historical tables such as "Andamento storico dei prelievi", yearly overviews,
       and any table showing multiple past months/years side by side. These are NOT the invoiced period.
    3. A typical invoice covers exactly ONE billing period (e.g. one month).
    4. A date range spanning a full year (e.g. January 1 to December 31) is NEVER a valid billing
       period — it is always a historical summary. Reject it and look for the actual invoice month.
    5. The kwh_quantity MUST correspond to the same billing period as start_date/end_date.
       Do not mix kWh values from one period with dates from another.

    WHERE TO FIND EACH FIELD:
    - supplier: the energy company name, usually at the top of the first page.
    - start_date / end_date: look in "importi riferiti al mese di", "periodo di fornitura",
      "fornitura dal ... al ...", or "dettagli riferiti alla fattura". Format: YYYY-MM-DD.
    - kwh_quantity: total active energy in kWh for the invoiced billing period only.
      If split by time bands (F1, F2, F3), SUM those kWh values from the "Misure" or
      "Dettaglio dei consumi" section. Do NOT use kWh values from historical/annual summary tables.
    If supplier, start_date, end_date or kwh_quantity is not present, set it to null.
    Always return exactly one entry in the periodes list."""

    periodes: List[Periode] = []


# Page validation rules for PageValidator — filter out irrelevant pages before LLM processing
PAGE_VALIDATION_RULES = [
    {"patterns": [r"importi riferiti|periodo.*fornitura|fornitura.*dal.*al"], "description": "Billing period declaration"},
    {"patterns": [r"misur[ea]|lettur[ae]", r"F[123].*kWh|energia attiva"], "description": "Meter readings"},
    {"patterns": [r"dettaglio dei consumi|servizi di vendita"], "description": "Consumption detail"},
    {"patterns": [r"totale.*fattura|netto.*pagare|sintesi.*fattura"], "description": "Invoice summary"},
]
