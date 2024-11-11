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
        "User-Agent": "doi_lookup/1.0 (https://github.com/TCKDB/TCKDB; mailto:calvin.p@campus.technion.ac.il)"
    }
    try:
        response = requests.get(f"{BASE_URL}{doi}", headers=headers, timeout=10)
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
    except requests.exceptions.HTTPError as http_err:
        print(f"HTTP error occurred: {http_err}")
        return None
    except requests.exceptions.ConnectionError as conn_err:
        print(f"Connection error occurred: {conn_err}")
        return None
    except requests.exceptions.Timeout as timeout_err:
        print(f"Timeout error occurred: {timeout_err}")
        return None
    except requests.exceptions.RequestException as req_err:
        print(f"Request exception occurred: {req_err}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return None
