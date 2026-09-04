"""Pydantic-Schemas für alle Zwischenstände (JSON in out/<job>/)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class RawAttribute(BaseModel):
    name: str = Field(description="Attributname wie im Katalog, z. B. 'Nennweite'")
    value: str = Field(description="Wert wie im Katalog, inkl. Einheit, z. B. 'DN 20' oder '1/2 Zoll'")


class Product(BaseModel):
    supplier_pid: str = Field(description="Artikelnummer des Herstellers, exakt wie im Katalog")
    name: str = Field(description="Kurzbezeichnung (max. 80 Zeichen)")
    description: str = Field(default="", description="Langtext / Beschreibung aus dem Katalog")
    gtin: Optional[str] = Field(default=None, description="EAN/GTIN falls angegeben")
    attributes: list[RawAttribute] = Field(default_factory=list)
    page: int = Field(default=0, description="Seite im Katalog (1-basiert)")
    source_quote: str = Field(default="", description="Wörtliches Zitat der Katalogzeile(n), aus denen der Artikel stammt")


class ExtractionResult(BaseModel):
    products: list[Product]
    notes: str = Field(default="", description="Auffälligkeiten: Tabellen ohne Artikelnummern, unklare Varianten etc.")


class ClassCandidate(BaseModel):
    class_id: str
    description: str
    score: float


class ClassDecision(BaseModel):
    class_id: Optional[str] = Field(description="Gewählte ETIM-Klasse (EC-Code) oder null, wenn keine passt")
    confidence: float = Field(ge=0, le=1)
    reasoning: str = Field(description="Ein Satz: warum diese Klasse, warum nicht die zweitbeste")
    runner_up: Optional[str] = Field(default=None, description="Zweitbeste Klasse (EC-Code)")


class ClassifiedProduct(BaseModel):
    product: Product
    candidates: list[ClassCandidate]
    decision: ClassDecision
    needs_review: bool = False


class FeatureValue(BaseModel):
    feature_id: str
    value: Optional[str] = Field(description="EV-Code bei Werteliste, Zahl (Punkt als Dezimaltrenner) bei numerisch, 'true'/'false' bei logisch, 'min;max' bei Range; null wenn nicht ermittelbar")
    value_max: Optional[str] = Field(default=None, description="nur bei Range-Merkmalen: oberer Wert")
    source: Optional[str] = Field(default=None, description="Wörtliches Zitat aus dem Katalog, das den Wert belegt")
    confidence: float = Field(ge=0, le=1)
    reason: Optional[str] = Field(default=None, description="Wenn value null: warum (nicht im Katalog / mehrdeutig / Einheit unklar)")


class FeatureFill(BaseModel):
    features: list[FeatureValue]


class EnrichedProduct(BaseModel):
    product: Product
    class_id: str
    class_desc: str
    class_confidence: float
    features: list[FeatureValue]
    feature_meta: dict[str, dict] = Field(default_factory=dict, description="feature_id -> {desc,type,unit_id,unit_desc,n_values}")
    needs_review: bool = False


class Job(BaseModel):
    job: str
    supplier_name: str
    source_file: str
    etim_version: str
