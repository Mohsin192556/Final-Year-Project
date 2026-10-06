import os
from functools import lru_cache
from typing import Any

from supabase import Client, create_client


class SupabaseConfigurationError(RuntimeError):
    pass


def storage_bucket() -> str:
    return os.getenv("SUPABASE_STORAGE_BUCKET", "qanoon-legal-documents")


@lru_cache(maxsize=1)
def supabase_client() -> Client:
    url = os.getenv("SUPABASE_URL", "").strip()
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not service_key:
        raise SupabaseConfigurationError(
            "Configure SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY."
        )
    return create_client(url, service_key)


def response_rows(response: Any) -> list[dict[str, Any]]:
    rows = getattr(response, "data", None)
    if not isinstance(rows, list):
        raise RuntimeError("Supabase returned an invalid table response.")
    return rows


def response_row(response: Any) -> dict[str, Any]:
    rows = response_rows(response)
    if len(rows) != 1:
        raise RuntimeError("Supabase did not return the expected record.")
    return rows[0]
