"""Reviewable species names from PubChem, fetched by an operator and loaded offline.

Nothing here runs at request time. ``fetch`` turns a list of species into a review file; ``app.services.
species_name_import`` loads a reviewed file. Only InChIKeys are sent to PubChem.
"""
