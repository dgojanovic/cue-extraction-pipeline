"""Shared data models for invoice extraction outputs."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class FieldStatus(StrEnum):
    FOUND = "found"
    UNKNOWN = "unknown"
    INVALID = "invalid"


class ConfidenceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class ExtractedField(BaseModel):
    """A single extracted value plus enough evidence to audit it."""

    raw_value: str | None = None
    normalized_value: str | None = None
    evidence: str | None = None
    status: FieldStatus = FieldStatus.UNKNOWN
    confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN


class LineItem(BaseModel):
    """Optional invoice line item extracted from the document."""

    name: ExtractedField = Field(default_factory=ExtractedField)
    quantity: ExtractedField = Field(default_factory=ExtractedField)
    amount: ExtractedField = Field(default_factory=ExtractedField)
    currency: ExtractedField = Field(default_factory=ExtractedField)


class InvoiceTotals(BaseModel):
    """Invoice-level financial summary."""

    pre_tax_amount: ExtractedField = Field(default_factory=ExtractedField)
    tax_percentage: ExtractedField = Field(default_factory=ExtractedField)
    tax_amount: ExtractedField = Field(default_factory=ExtractedField)
    discount: ExtractedField = Field(default_factory=ExtractedField)
    total_amount: ExtractedField = Field(default_factory=ExtractedField)


class InvoiceExtraction(BaseModel):
    """Structured extraction result for a single invoice PDF."""

    document_name: str
    extraction_method: str = "unknown"
    invoice_id: ExtractedField = Field(default_factory=ExtractedField)
    supplier_name: ExtractedField = Field(default_factory=ExtractedField)
    currency: ExtractedField = Field(default_factory=ExtractedField)
    invoice_date: ExtractedField = Field(default_factory=ExtractedField)
    due_date: ExtractedField = Field(default_factory=ExtractedField)
    po_reference: ExtractedField = Field(default_factory=ExtractedField)
    totals: InvoiceTotals = Field(default_factory=InvoiceTotals)
    line_items: list[LineItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExtractionError(BaseModel):
    """Non-crashing failure record for one document."""

    document_name: str
    reason: str
    attempted_steps: list[str] = Field(default_factory=list)


class FieldValidation(BaseModel):
    """Validation result for one extracted field."""

    field_path: str
    value: str | None = None
    normalized_value: str | None = None
    matched_candidate: str | None = None
    reason: str


class ValidatedInvoiceExtraction(BaseModel):
    """LLM extraction wrapped with deterministic candidate validation."""

    document_name: str
    extraction: InvoiceExtraction
    valid_fields: list[FieldValidation] = Field(default_factory=list)
    invalid_fields: list[FieldValidation] = Field(default_factory=list)
    unchecked_fields: list[FieldValidation] = Field(default_factory=list)
    candidate_warnings: list[str] = Field(default_factory=list)
    source_warnings: list[str] = Field(default_factory=list)
