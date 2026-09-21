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
    model: str = Field(default="gemini", description="Welches Modell die Klasse gewählt hat: 'gemini' oder 'jev'")
    etim_version: str = Field(default="", description="Gegen welche ETIM-Version klassifiziert wurde, z. B. '10.0'")
    simulated: bool = Field(default=False, description="Antwort aus dem Trockenlauf, nicht gemessen")


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
    coverage: float = Field(default=0.0, description="Anteil der befuellten Merkmale (0..1)")
    model: str = Field(default="gemini", description="Modell der Klassenentscheidung")
    etim_version: str = Field(default="", description="ETIM-Version, aus der Merkmale und Wertelisten stammen")
    needs_review: bool = False


class Job(BaseModel):
    job: str
    supplier_name: str
    source_file: str
    etim_version: str


# --- Modellvergleich Gemini gegen Jev ------------------------------------------


class ReasoningCode(BaseModel):
    """Ein EC-Code, den ein Modell in seiner Begründung genannt hat, gegengeprüft."""
    code: str
    known: bool = Field(description="Existiert der Code in der geladenen ETIM-Klassentabelle?")
    desc: str = Field(default="", description="Klassentext, falls bekannt")


class ModelAnswer(BaseModel):
    """Die Antwort eines Modells auf einen Artikel — vergleichbar gemacht."""
    model: str = Field(description="'gemini' oder 'jev'")
    class_id: Optional[str] = None
    confidence: float = 0.0
    reasoning: str = Field(default="", description="Nur Gemini — Jev erzeugt keinen Text")
    reasoning_codes: list[ReasoningCode] = Field(default_factory=list)
    runner_up: Optional[str] = None
    probabilities: dict[str, float] = Field(default_factory=dict, description="Nur Jev, gekürzt auf die stärksten Optionen")
    is_accessory: Optional[float] = Field(default=None, description="Nur Jev: Noul-Wahrscheinlichkeit 'Zubehör/Ersatzteil'")
    candidates_seen: int = 0
    rank_of_choice: Optional[int] = Field(default=None, description="Rang der gewählten Klasse in der Retrieval-Liste (1-basiert)")
    trim_level: str = Field(default="", description="Nur Jev: wie stark die Optionsbeschreibungen gekürzt wurden")
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    simulated: bool = False
    error: Optional[str] = None


class FeatureAnswer(BaseModel):
    """Ein Merkmalswert eines Modells, mit Belegstatus."""
    feature_id: str
    value: Optional[str] = None
    confidence: float = 0.0
    source: Optional[str] = Field(default=None, description="Quellzitat — bei Jev immer von Gemini übernommen")
    source_from: str = Field(default="", description="'gemini' wenn der Beleg von Gemini stammt")
    exportable: bool = Field(default=False, description="Wert belegt und damit exportfähig")
    reason: Optional[str] = None


class FeatureComparison(BaseModel):
    supplier_pid: str
    class_id: str
    feature_meta: dict[str, dict] = Field(default_factory=dict)
    answers: dict[str, list[FeatureAnswer]] = Field(default_factory=dict)
    jev_skipped: dict[str, str] = Field(default_factory=dict, description="feature_id -> Grund, warum Jev nicht gefragt wurde")
    jev_latency_ms: int = 0
    jev_cost_usd: float = 0.0
    jev_simulated: bool = False
    error: Optional[str] = None


class ComparisonItem(BaseModel):
    product: Product
    base_name: str = Field(description="Bezeichnung ohne Variantenteil — Grundlage der Konsistenzprüfung")
    reference_class: Optional[str] = None
    reference_rank: Optional[int] = Field(default=None, description="Rang der Referenzklasse in der Retrieval-Liste")
    retrieval: list[ClassCandidate] = Field(default_factory=list)
    answers: dict[str, ModelAnswer] = Field(default_factory=dict)


class ModelMetrics(BaseModel):
    model: str
    label: str
    model_id: str = Field(default="", description="Modellkennung, getrennt vom Anzeigenamen")
    n: int = 0
    errors: int = 0
    simulated: bool = False
    no_class: int = 0
    candidates_seen: int = 0
    hits: Optional[int] = None
    hit_rate: Optional[float] = None
    reference_in_window: Optional[int] = Field(default=None, description="Wie oft die Referenzklasse überhaupt im Kandidatenfeld lag")
    variant_groups: int = 0
    variant_consistent: int = 0
    variant_consistency: Optional[float] = None
    reasoning_codes: int = 0
    unverified_codes: int = 0
    conf_correct: Optional[float] = None
    conf_wrong: Optional[float] = None
    latency_ms_avg: int = 0
    latency_ms_total: int = 0
    cost_usd: float = 0.0
    # Merkmale
    feat_articles: int = 0
    feat_total: int = 0
    feat_filled: int = 0
    feat_exportable: int = 0
    feat_cost_usd: float = 0.0
    feat_latency_ms_total: int = 0


class Comparison(BaseModel):
    job: str
    created: str
    etim_version: str = ""
    gemini_model: str = ""
    jev_model: str = ""
    jev_status: dict = Field(default_factory=dict)
    has_reference: bool = False
    n_reference: int = 0
    reused_gemini: bool = False
    with_features: bool = False
    top_k_gemini: int = 0
    top_k_jev: int = 0
    items: list[ComparisonItem] = Field(default_factory=list)
    features: list[FeatureComparison] = Field(default_factory=list)
    metrics: dict[str, ModelMetrics] = Field(default_factory=dict)
    agreement: dict = Field(default_factory=dict, description="Übereinstimmung der Modelle untereinander")
    notes: list[str] = Field(default_factory=list)
