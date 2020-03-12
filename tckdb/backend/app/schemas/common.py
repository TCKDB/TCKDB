"""
TCKDB backend app schemas common module
"""


def lowercase_dict(dictionary: dict) -> dict:
    """
    Convert all string keys and values in a dictionary to lowercase.

    Args:
        dictionary (dict): A dictionary to process.

    Raises:
        TypeError: If ``dictionary`` is not a ``dict`` instance.

    Returns:
        dict: A dictionary with all string keys and values lowercase.
    """
    if not isinstance(dictionary, dict):
        raise TypeError(f'Expected a dictionary, got a {type(dictionary)}')
    new_dict = dict()
    for key, val in dictionary.items():
        new_key = key.lower() if isinstance(key, str) else key
        if isinstance(val, dict):
            val = lowercase_dict(val)
        new_val = val.lower() if isinstance(val, str) else val
        new_dict[new_key] = new_val
    return new_dict
