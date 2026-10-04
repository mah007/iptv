"""GET /api/v1/search (SPEC §7.8, §10): titles and people the customer may browse."""

from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response

from apps.catalog.api_customer import CatalogView
from apps.core.schema import problems
from apps.search import services
from apps.search.serializers import SearchQuerySerializer, SearchResultSerializer


class SearchView(CatalogView):
    @extend_schema(
        operation_id="search",
        summary="Search movies, series, episodes and people (Arabic and English)",
        parameters=[SearchQuerySerializer],
        responses={200: SearchResultSerializer, **problems(400, 401, 403)},
    )
    def get(self, request: Request) -> Response:
        query = SearchQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data
        result = services.search(
            self.scope(),
            data["q"],
            services.Filters(type=data.get("type"), genre=data.get("genre"), year=data.get("year")),
            page=data["page"],
            page_size=data["page_size"],
        )
        return Response(SearchResultSerializer(result, context=self.context()).data)
