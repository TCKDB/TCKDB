import sys

from molecule.exceptions import InvalidAdjacencyListError
from molecule.molecule.adjlist import from_adjacency_list

# def is_valid_adjlist(adjlist: str) -> Tuple[bool, str]:
#     """
#     Checks whether a string represents a valid adjacency list.

#     Args:
#         adjlist (str): The string to be checked.

#     Returns:
#         Tuple[bool, str]:
#             - Whether the string represents a valid adjacency list.
#             - A reason for invalidating the argument.
#     """
#     if not isinstance(adjlist, str):
#         return False, f'An adjacency list must be a string, got "{adjlist}" which is a {type(adjlist)}.'
#     try:
#         from_adjacency_list(adjlist=adjlist, group=False, saturate_h=False)
#     except InvalidAdjacencyListError as e:
#         return False, str(e)
#     except Exception as e:
#         return False, f'An error occurred: {e}'
#     return True, ''


def main():
    if len(sys.argv) > 1:
        adjlist = sys.argv[1]
    else:
        adjlist = sys.stdin.read()

    if not adjlist.strip():
        print("Error: No adjacency list provided.", file=sys.stderr)
        sys.exit(1)

    if not isinstance(adjlist, str):
        return print(False), print(
            f'An adjacency list must be a string, got "{adjlist}" which is a {type(adjlist)}.'
        )
    try:
        from_adjacency_list(adjlist=adjlist, group=False, saturate_h=False)
    except InvalidAdjacencyListError as e:
        return print(False), print(str(e))
    except Exception as e:
        return print(False), print(f"An error occurred: {e}")
    return print(True), print("")


if __name__ == "__main__":
    main()
