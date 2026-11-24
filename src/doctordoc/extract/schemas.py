"""Ready-made schemas: invoice, receipt, daily drilling report, generic form.

A schema is a normal Pydantic model. The `description` of each field is what the LLM reads,
so write it like an instruction. Rules that need several fields (totals, dates in order)
live in `doctordoc_rules`, a class attribute that is not part of the data.
"""

from __future__ import annotations

from datetime import date
from typing import ClassVar

from pydantic import BaseModel, Field

from doctordoc.extract.validation import (
    Rule,
    date_order,
    in_range,
    required,
    row_product,
    sum_equals,
    sum_of_fields,
)


class LineItem(BaseModel):
    description: str | None = Field(None, description="What was sold, as written on the line")
    quantity: float | None = Field(None, description="Number of units")
    unit_price: float | None = Field(None, description="Price of one unit, plain number")
    amount: float | None = Field(
        None, description="Line total (quantity x unit price), plain number"
    )


class Invoice(BaseModel):
    """A supplier invoice."""

    invoice_number: str | None = Field(
        None, description="The invoice number or ID, exactly as printed"
    )
    invoice_date: date | None = Field(None, description="Date the invoice was issued (YYYY-MM-DD)")
    due_date: date | None = Field(None, description="Payment due date (YYYY-MM-DD)")
    vendor_name: str | None = Field(None, description="Company that issued the invoice")
    customer_name: str | None = Field(None, description="Company or person being billed")
    currency: str | None = Field(None, description="3-letter currency code such as USD or EUR")
    line_items: list[LineItem] = Field(default_factory=list, description="Every invoiced line")
    subtotal: float | None = Field(None, description="Total before tax, plain number")
    tax_amount: float | None = Field(None, description="Tax in money (not the percentage)")
    total: float | None = Field(None, description="Final amount to pay, plain number")

    doctordoc_rules: ClassVar[list[Rule]] = [
        required("invoice_number", "invoice_date", "total"),
        date_order("invoice_date", "due_date"),
        in_range("total", minimum=0),
        row_product("line_items", "quantity", "unit_price", "amount"),
        sum_equals("line_items[*].amount", "subtotal"),
        sum_of_fields(["subtotal", "tax_amount"], "total"),
    ]


class ReceiptItem(BaseModel):
    name: str | None = Field(None, description="Item name as printed")
    quantity: float | None = Field(None, description="Number of units, 1 if not shown")
    price: float | None = Field(None, description="Price of this line, plain number")


class Receipt(BaseModel):
    """A shop or restaurant receipt."""

    merchant_name: str | None = Field(None, description="Name of the shop or restaurant")
    purchase_date: date | None = Field(None, description="Date of purchase (YYYY-MM-DD)")
    items: list[ReceiptItem] = Field(default_factory=list, description="Every purchased line")
    subtotal: float | None = Field(None, description="Total before tax, plain number")
    tax: float | None = Field(None, description="Tax in money")
    total: float | None = Field(None, description="Total paid, plain number")
    payment_method: str | None = Field(None, description="cash, card, or the card brand if shown")
    currency: str | None = Field(None, description="3-letter currency code such as USD")

    doctordoc_rules: ClassVar[list[Rule]] = [
        required("merchant_name", "total"),
        in_range("total", minimum=0),
        sum_equals("items[*].price", "subtotal"),
        sum_of_fields(["subtotal", "tax"], "total"),
    ]


class Activity(BaseModel):
    from_time: str | None = Field(
        None,
        pattern=r"^\d{1,2}:\d{2}$",
        description="Start time of the activity, HH:MM only (e.g. 06:00)",
    )
    to_time: str | None = Field(
        None,
        pattern=r"^\d{1,2}:\d{2}$",
        description="End time of the activity, HH:MM only (e.g. 08:30)",
    )
    hours: float | None = Field(None, description="Duration in hours")
    description: str | None = Field(None, description="What the crew did, as written")


class DailyDrillingReport(BaseModel):
    """A daily drilling report (DDR) from a rig."""

    report_date: date | None = Field(None, description="Date of the report (YYYY-MM-DD)")
    well_name: str | None = Field(None, description="Name or number of the well")
    rig_name: str | None = Field(None, description="Name of the drilling rig")
    operator: str | None = Field(None, description="Operating company")
    depth_start_m: float | None = Field(None, description="Measured depth at start of day, metres")
    depth_end_m: float | None = Field(None, description="Measured depth at end of day, metres")
    progress_m: float | None = Field(None, description="Metres drilled during the day")
    bit_size_in: float | None = Field(None, description="Bit size in inches")
    avg_rop_m_per_hr: float | None = Field(None, description="Average rate of penetration, m/hr")
    mud_weight_ppg: float | None = Field(None, description="Mud weight in pounds per gallon")
    mud_type: str | None = Field(None, description="Mud type, e.g. water-based, OBM")
    npt_hours: float | None = Field(None, description="Non-productive time in hours")
    weather: str | None = Field(None, description="Weather remarks")
    activities: list[Activity] = Field(default_factory=list, description="24-hour operations log")

    doctordoc_rules: ClassVar[list[Rule]] = [
        required("report_date", "well_name"),
        in_range("depth_start_m", minimum=0, maximum=15000),
        in_range("depth_end_m", minimum=0, maximum=15000),
        in_range("avg_rop_m_per_hr", minimum=0, maximum=500),
        in_range("mud_weight_ppg", minimum=6, maximum=25),
        in_range("npt_hours", minimum=0, maximum=24),
        in_range("activities[*].hours", minimum=0, maximum=24),
        sum_of_fields(["depth_start_m", "progress_m"], "depth_end_m", tolerance=1.0),
    ]


class FormEntry(BaseModel):
    label: str = Field(description="The field name or question printed on the form")
    value: str | None = Field(None, description="What was filled in, as written; null if blank")


class GenericForm(BaseModel):
    """Any form: a title and a list of label/value pairs."""

    title: str | None = Field(None, description="Title of the form")
    entries: list[FormEntry] = Field(default_factory=list, description="Every labelled field")


BUILTIN_SCHEMAS: dict[str, type[BaseModel]] = {
    "invoice": Invoice,
    "receipt": Receipt,
    "drilling": DailyDrillingReport,
    "form": GenericForm,
}
