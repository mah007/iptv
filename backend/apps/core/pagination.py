from typing import Any

from rest_framework import pagination


class PageNumberPagination(pagination.PageNumberPagination):
    """Admin tables (SPEC §10: page numbers under 10k rows): ?page=N&page_size=M."""

    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        """The response envelope in OpenAPI 3.1 terms; every key is always present."""
        return {
            "type": "object",
            "required": ["count", "next", "previous", "results"],
            "properties": {
                "count": {"type": "integer", "example": 123},
                "next": {"type": ["string", "null"], "format": "uri"},
                "previous": {"type": ["string", "null"], "format": "uri"},
                "results": schema,
            },
        }
