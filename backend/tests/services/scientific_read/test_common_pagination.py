"""Regression tests for composed-search deep pagination."""

from types import SimpleNamespace

import pytest

from app.api.config import settings
from app.schemas.reads.scientific_common import Pagination
from app.services.scientific_read.common import collect_bounded_pages


def test_collect_bounded_pages_walks_past_first_page(monkeypatch):
    monkeypatch.setattr(settings, "public_max_limit", 2)
    values = list(range(5))
    offsets: list[int] = []

    def fetch_page(offset: int, limit: int):
        offsets.append(offset)
        records = values[offset : offset + limit]
        return SimpleNamespace(
            records=records,
            pagination=Pagination(
                offset=offset,
                limit=limit,
                returned=len(records),
                total=len(values),
            ),
        )

    assert collect_bounded_pages(fetch_page, resource_name="test") == values
    assert offsets == [0, 2, 4]


def test_collect_bounded_pages_rejects_unreachable_total(monkeypatch):
    monkeypatch.setattr(settings, "public_max_limit", 2)
    monkeypatch.setattr(settings, "public_max_offset", 3)

    def fetch_page(offset: int, limit: int):
        return SimpleNamespace(
            records=[0, 1],
            pagination=Pagination(
                offset=offset,
                limit=limit,
                returned=2,
                total=6,
            ),
        )

    with pytest.raises(ValueError, match="composed_search_candidate_limit_exceeded"):
        collect_bounded_pages(fetch_page, resource_name="test")
