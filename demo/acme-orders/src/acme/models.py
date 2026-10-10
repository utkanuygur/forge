"""Request and response models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

OrderStatus = Literal["pending", "paid", "shipped"]


class LineItem(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    quantity: int = Field(gt=0, le=1000)
    unit_price_cents: int = Field(ge=0)


class OrderCreate(BaseModel):
    customer_email: str = Field(min_length=3, max_length=254)
    items: list[LineItem] = Field(min_length=1)
    discount_percent: int = Field(default=0, ge=0, le=100)


class Order(BaseModel):
    id: str
    customer_email: str
    items: list[LineItem]
    status: OrderStatus
    discount_percent: int
    subtotal_cents: int
    discount_cents: int
    total_cents: int
    created_at: datetime
    updated_at: datetime
