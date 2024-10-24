from typing import Dict, Optional

import requests

BASE_URL = "https://api.crossref.org/works/"


def fetch_doi_metadata(doi: str) -> Optional[Dict[str, any]]:
    """
    Fetches metadata for a given DOI using the CrossRef API.

    Args:
        doi (str): The DOI to look up.

    Returns:
        Optional[Dict[str, any]]: A dictionary containing metadata if found, else None.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
    }
    try:
        response = requests.get(f"{BASE_URL}{doi}", headers=headers)
        response.raise_for_status()
        api_data = response.json()
        if "message" in api_data:
            metadata = api_data["message"]
            normalized_metadata = {
                "DOI": metadata.get("DOI"),
                "ISSN": metadata.get("ISSN"),
                "URL": metadata.get("URL"),
                "abstract": metadata.get("abstract"),
                "author": metadata.get("author"),
                "container-title": metadata.get("container-title"),
                "issued": metadata.get("issued", {}).get("date-parts", [[None]])[0][0],
                "publisher": metadata.get("publisher"),
                "page": metadata.get("page"),
                "volume": metadata.get("volume"),
                "issue": metadata.get("issue"),
                "title": metadata.get("title", [None])[0],
                "language": metadata.get("language"),
            }
            return normalized_metadata
        else:
            return None
    except requests.exceptions.HTTPError:
        return None
    except requests.exceptions.ConnectionError:
        return None
    except requests.exceptions.RequestException:
        return None
    except Exception:
        return None
