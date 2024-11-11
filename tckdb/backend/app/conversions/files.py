"""
TCKDB backend app conversions files module
This module is used for dumping and loading various file types
"""

import os

import yaml


def read_yaml_file(path: str) -> dict or list:
    """
    Read a YAML file and return the parameters as python variables.

    Args:
        path (str): The YAML file path to read.

    Returns:
        Union[dict, list]: The content read from the file.
    """
    if not isinstance(path, str):
        raise ValueError(f"path must be a string, got {path} which is a {type(path)}")
    if not os.path.isfile(path):
        raise ValueError(f"Could not find the YAML file {path}")
    with open(path, "r") as f:
        content = yaml.safe_load(stream=f, Loader=yaml.FullLoader)
    return content


def save_yaml_file(
    path: str,
    content: list or dict,
) -> None:
    """
    Save a YAML file.

    Args:
        path (str): The YAML file path to save.
        content (list, dict): The content to save.
    """
    if not isinstance(path, str):
        raise ValueError(f"path must be a string, got {path} which is a {type(path)}")
    yaml.add_representer(str, string_representer)
    content = yaml.dump(data=content)
    with open(path, "w") as f:
        f.write(content)


def string_representer(dumper, data):
    """
    Add a custom string representer to use block literals for multiline strings in YAML files.
    """
    if len(data.splitlines()) > 1:
        return dumper.represent_scalar(
            tag="tag:yaml.org,2002:str", value=data, style="|"
        )
    return dumper.represent_scalar(tag="tag:yaml.org,2002:str", value=data)
