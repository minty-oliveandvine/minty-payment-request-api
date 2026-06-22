from ninja import Schema


class PaginatedResponse(Schema):
    count: int
    page: int
    page_size: int
    results: list


def paginate_queryset(queryset, page: int = 1, page_size: int = 20):
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    total = queryset.count()
    offset = (page - 1) * page_size
    items = list(queryset[offset : offset + page_size])
    return {
        "count": total,
        "page": page,
        "page_size": page_size,
        "results": items,
    }
