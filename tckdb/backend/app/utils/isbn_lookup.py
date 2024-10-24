# tckdb/backend/app/utils/isbn_lookup.py

from typing import Dict, Optional

import isbnlib


def fetch_isbn_metadata(isbn: str) -> Optional[Dict[str, any]]:
    """
    Fetches metadata for a given ISBN using isbnlib.

    Args:
        isbn (str): The ISBN number to look up.

    Returns:
        Optional[Dict[str, any]]: A dictionary containing metadata if found, else None.
    """
    try:
        metadata = isbnlib.meta(isbn)
        if metadata:
            # Normalize keys if necessary
            normalized_metadata = {
                "Title": metadata.get("Title"),
                "Authors": metadata.get("Authors"),
                "Publisher": metadata.get("Publisher"),
                "Year": int(metadata.get("Year")) if metadata.get("Year") else None,
                "Language": metadata.get("Language"),
                "ISBN": metadata.get("ISBN"),
            }
            return normalized_metadata
        return None
    except Exception as e:
        # Log the exception as needed
        print(f"Error fetching metadata for ISBN {isbn}: {e}")
        return None
