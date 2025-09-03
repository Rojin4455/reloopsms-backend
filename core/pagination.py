from rest_framework.pagination import PageNumberPagination

class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20  # default
    page_size_query_param = "per_page"  # allow ?per_page=
    max_page_size = 100  # safety limit
