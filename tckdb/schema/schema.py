"""
A schema representing objects in TCKDB
"""

from fastapi import FastAPI


app = FastAPI()


@app.get("/")
async def root():
    """Links to schema items"""
    links = {'author': 'http://127.0.0.1:8000/author/',
             'bot': 'http://127.0.0.1:8000/bot/',
             'literature': 'http://127.0.0.1:8000/literature/',
             'species': 'http://127.0.0.1:8000/species/',
             'reaction': 'http://127.0.0.1:8000/reaction/',
             'network (x)': 'http://127.0.0.1:8000/network/',
             'energy transfer model': 'http://127.0.0.1:8000/energy_transfer_model/',
             'L-J': 'http://127.0.0.1:8000/L-J/',
             'frequency scaling factor': 'http://127.0.0.1:8000/freq_scaling/',
             'energy corrections (x)': 'http://127.0.0.1:8000/en_corr/',
            }
    return links


cartesian_coordinates = {
    'symbols': {
        'type': 'array',
        'description': 'The atomic symbols of the chemical elements in the molecule',
        'examples': [('C', 'C', 'O', 'H', 'H', 'H', 'H', 'H', 'H')],
        'minItems': 1,
    },
    'isotopes': {
        'type': 'array',
        'description': 'The isotope number corresponding to elements in the molecule '
                       '(most common isotopes assumed if not specified)',
        'examples': [(12, 12, 16, 1, 1, 1, 1, 1, 1)],
        'minItems': 1,
    },
    'coords': {
        'type': 'array',
        'description': 'The Cartesian coordinates in standard orientation '
                       'corresponding to elements in the molecule',
        'minItems': 1,
        'items': {
            'type': 'array',
            'description': 'The coordinates of a single atom',
            'items': {
                'type': 'number',
            },
            'minItems': 3,
            'maxItems': 3,
        },
        'examples': [((-0.9745985253315856, 0.2918114947632385, 0.1030526817808688),
                      (0.395710388741102, -0.3513242815942441, 0.10195666665384644),
                      (0.30300093848092235, -1.6364752129565114, -0.4942362477616512),
                      (-1.6896626666808134, -0.3245092837674076, 0.6579679942916276),
                      (-0.938939631757634, 1.286162315910681, 0.5567782628696178),
                      (-1.3589226982360818, 0.38287268675366914, -0.9182710061329392),
                      (0.7681171143874449, -0.4632859746979136, 1.124618942762531),
                      (1.1033080093414405, 0.2536171714070304, -0.47285772230980083),
                      (1.1953532398500444, -2.0225382163275363, -0.4802607317299328))],
    },
}


geometry_parameters = {
    'type': 'object',
    'description': 'Any constraints used in the optimization '
                   'other than the primary torsion modes',
    'default': None,
    'required': ['atoms', 'values'],
    'properties': {
        'atoms': {
            'type': 'array',
            'description': 'Geometry parameters (bond, angle or dihedral) '
                           'consisting of atom indices and the parameter value',
            'items': {
                'type': 'array',
                'description': 'Atom indices (1-indexed) of the parameter. '
                               'A length 2, 3, or 4 list corresponds to bond, angle or dihedral, respectively.',
                'items': {
                    'type': 'number',
                    'description': 'An atom index',
                },
                'minItems': 2,
                'maxItems': 4,
            },
        },
        'values': {
            'type': 'array',
            'description': 'The parameter value corresponding to atom entries',
            'items': 'number',
        },
        'units': {
            'type': 'array',
            'description': 'Length and angle units',
            'items': 'string',
            'minItems': 2,
            'maxItems': 2,
            'default': ['angstroms', 'degrees'],
        },
    },
    'backend': 'from str, from file (input, output, XYZ)',
}


level_of_theory_properties = {
    'method': {
        'type': 'string',
        'description': 'The method used for the computation',
    },
    'basis': {
        'type': 'string',
        'description': 'The basis set used for the computation',
    },
    'dispersion': {
        'type': 'string',
        'description': 'The DFT dispersion type used for the computation',
    },
    'auxiliary_basis': {
        'type': 'string',
        'description': 'The auxiliary basis set(s) used for the computation',
    },
    'solvation': {
        'type': 'object',
        'description': 'The solvation method and solvent',
        'required': ['method'],
        'properties': {
            'method': {
                'type': 'string',
                'description': 'The solvation method used',
            },
            'solvent': {
                'type': 'string',
                'description': 'The solvent used',
            },
        },
    },
    'backend': 'check l.o.t',
}

ess_properties = {
    'software': {
        'type': 'string',
        'description': 'An electronic structure software name',
    },
    'version': {
        'type': 'string',
        'description': 'An electronic structure software version',
    }
}


proveance = {
    'type': 'object',
    'description': 'Author and bot information',
    'required': ['author'],
    'properties': {
        'author': {
            'type': 'integer',
            'description': 'An author ID',
            'backend': 'check validity, map author vs. object for the reviewer',
        },
        'bot': {
            'type': 'integer',
            'description': 'A bot ID',
            'backend': 'map bot vs. object for the reviewer',
        },
        'timestamp': {
            'type': 'string',
            'description': 'The timestamp of uploading the data to TCKDB (automatically assigned)',
        },
    },
}  # proveance


review = {
    'type': 'object',
    'description': 'attributes related to the review process',
    'required': ['reviewer', 'reviewed', 'rejected'],
    'properties': {
        'reviewer': {
            'type': 'integer',
            'description': 'An author ID',
            'default': None,
        },
        'reviewed': {
            'type': 'boolean',
            'description': 'Whether this entry was reviewed and approved',
            'default': False,
        },
        'rejected': {
            'type': 'boolean',
            'description': 'Whether this entry was reviewed and rejected',
            'default': False,
        },
    },
}


@app.get("/author/")
async def author():
    """Defines an author schema"""

    author = {
        '$schema': 'http://json-schema.org/schema#',
        '$id': 'https://github.com/tckdb/TCKDB',
        'title': 'Author',
        'type': 'object',
        'description': 'The corresponding author information',
        'required': ['name', 'affiliation', 'email'],
        'properties': {
            'id': {
                'type': 'integer',
                'description': 'The TCKDB unique author identifier (automatically assigned)',
            },
            'name': {
                'type': 'string',
                'description': 'Author full name',
            },
            'affiliation': {
                'type': 'string',
                'description': 'Author affiliation',
            },
            'email': {
                'type': 'string',
                'description': 'Author email address',
            },
        },
    }

    return author


@app.get("/bot/")
async def bot():
    """Defines a bot schema"""

    bot = {
        '$schema': 'http://json-schema.org/schema#',
        '$id': 'https://github.com/tckdb/TCKDB',
        'title': 'Bot',
        'type': 'object',
        'description': 'The bot information (a bot is a software used to automatically generate data for TCKDB)',
        'required': ['name', 'affiliation', 'email'],
        'properties': {
            'id': {
                'type': 'integer',
                'description': 'The TCKDB unique bot identifier (automatically assigned)',
            },
            'name': {
                'type': 'string',
                'description': 'The software name',
            },
            'version': {
                'type': 'string',
                'description': 'The software version',
            },
            'url': {
                'type': 'string',
                'description': 'The software official web address(es) (separate with "; " if more than one)',
            },
        },
        'backend': 'check validity of name and URL',
    }

    return bot


@app.get("/literature/")
async def literature():
    """Defines a bot schema"""

    literature = {
        '$schema': 'http://json-schema.org/schema#',
        '$id': 'https://github.com/tckdb/TCKDB',
        'title': 'Literature',
        'type': 'object',
        'description': 'A literature reference',
        'required': ['name', 'affiliation', 'email'],
        'properties': {
            'reference type': {
                'type': 'string',
                'enum': ['article', 'thesis', 'book'],
            },
            'entry': {
                'type': 'string',
                'description': 'A LATEX valid bibref text',
            },
            'default': None,
            'backend': 'extract doi/ISBN/url and check',
        },
        'backend': 'check validity of name and URL',
    }

    return literature


@app.get("/species/")
async def species():
    """Defines a species schema"""

    species = {
        '$schema': 'http://json-schema.org/schema#',
        '$id': 'https://github.com/tckdb/TCKDB',
        'title': 'Species',
        'type': 'object',
        'description': 'The Species schema',
        'required': ['proveance', 'identifiers', 'charge', 'multiplicity', 'coordinates', 'external symmetry',
                     'chirality', 'conformation info', 'electronic energy', 'E0', 'hessian', 'torsions',
                     'H298', 'S298', 'Cp', 'energy corrections', 'statmech software', 'levels', 'ess', 'files',
                     ],

        'properties': {

            # general

            'id': {
                'type': 'integer',
                'description': 'The TCKDB unique species identifier (automatically assigned)',
            },  # id

            'proveance': proveance,

            'label': {
                'type': 'string',
                'description': 'A free user label for the species',
                'default': None,
                'examples': ['ethanol'],
                'backend': 'no line breaks',
            },  # label

            'review': review,

            'literature': {
                'type': 'integer',
                'description': 'A literature ID (can be amended)',
                'backend': 'check validity, map author vs. object for the reviewer',
            },  # literature

            'retracted': {
                'type': 'object',
                'description': 'Whether this object was retracted (can be amended)',
                'properties': {
                    'retracted': {
                        'type': 'boolean',
                        'default': 'False',
                    },
                    'reason': {
                        'type': 'string',
                        'description': 'A reason for retracting this object',
                    },
                },
                'default': {'retracted': False},
            },  # retracted

            'extras': {
                'type': 'object',
                'description': 'Any additional information in the form of a Python dictionary',
            },  # extras

            # chemistry

            'identifiers': {
                'type': 'object',
                'description': 'Chemical identifiers',
                'required': ['smiles', 'inchi'],
                'properties': {
                    'smiles': {
                        'type': 'string',
                        'description': 'The SMILES descriptor',  # canonical? chirality info?
                        'examples': ['COO'],
                    },
                    'inchi': {
                        'type': 'string',
                        'description': 'The InChI descriptor with the H layer and chirality',  # chirality info?
                        'examples': ['InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3'],
                    },
                },
                'backend': 'check validity, interconvert to check consistency',
            },  # identifiers

            'charge': {
                'type': 'integer',
                'description': 'The net molecular charge',
                'backend': 'verify from the identifiers',
            },  # charge

            'multiplicity': {
                'type': 'integer',
                'description': 'The spin multiplicity',
                'minimum': 1,
                'backend': 'verify from the identifiers',
            },  # multiplicity

            # geometry and connectivity

            'coordinates': {
                'type': 'object',
                'description': 'Cartesian coordinates in standard orientation',
                'required': ['symbols', 'coords'],
                'properties': cartesian_coordinates,
                'backend': 'perceive 2D connectivity and verify, check atom collisions',
            },  # coordinates

            'graphs': {
                'type': 'array',
                'description': 'A list of 2D graphs in an RMG adjacency list format, with the following information: '
                               'number of lone electron pairs per atom, number of radical electrons per atom, '
                               'formal charge per atom, connectivity with bond order information. '
                               'Each graph represents a localized Lewis structure, while collectively the graphs '
                               'represent all significant resonance structures of the species. '
                               'Can be generated from SMILES/InChI.',
                'items': {
                    'type': 'string',
                    'description': 'An RMG-style adjacency list describing a single Lewis structure',
                },
                'minItems': 1,
                'examples': ["""1 C u0 p0 c0 {2,S} {4,S} {5,S} {6,S}
2 C u0 p0 c0 {1,S} {3,S} {7,S} {8,S}
3 O u0 p2 c0 {2,S} {9,S}
4 H u0 p0 c0 {1,S}
5 H u0 p0 c0 {1,S}
6 H u0 p0 c0 {1,S}
7 H u0 p0 c0 {2,S}
8 H u0 p0 c0 {2,S}
9 H u0 p0 c0 {3,S}"""],
            },  # graphs

            'fragments': {
                'type': 'array',
                'description': 'Fragments represented by this species, e.g., a VdW well. Defaults to one fragment',
                'items': {
                    'type': 'array',
                    'description': 'The 1-indexed atom indices of all atoms in a fragment',
                    'items': {
                        'type': 'integer',
                        'description': 'An atom index',
                    },
                    'minimum': 1,
                    'minItems': 1,
                },
                'minItems': 1,
                'examples': [[1, 2, 3, 4, 5, 6, 7, 8, 9]],
                'backend': 'all atoms participate in fragments, no atom participates in more than one fragment',
            },  # fragments

            'fragment orientation': {
                'type': 'array',
                'description': 'Relative orientation of fragments starting from the heaviest one, both fragments are '
                               'in standard Cartesian orientation. The number of entries is the number of fragments '
                               'minus one.',
                'items': {
                    'type': 'object',
                    'description': 'The relative orientation of fragment i+1 relative to fragment i',
                    'required': ['cm', 'x', 'y', 'z'],
                    'properties': {
                        'cm': {
                            'type': 'array',
                            'description': 'A vector representing the relative position of the center of mass of '
                                           'fragment i+i relative to the center of mass of fragment i',
                            'items': {
                                'type': 'number',
                            },
                            'minItems': 3,
                            'maxItems': 3,
                        },
                        'x': {
                            'type': 'number',
                            'description': 'The angle formed between the x axis of fragment i+i in standard '
                                           'orientation and the x axis of fragment i in standard orientation',
                        },
                        'y': {
                            'type': 'number',
                            'description': 'The angle formed between the y axis of fragment i+i in standard '
                                           'orientation and the y axis of fragment i in standard orientation',
                        },
                        'z': {
                            'type': 'number',
                            'description': 'The angle formed between the z axis of fragment i+i in standard '
                                           'orientation and the z axis of fragment i in standard orientation',
                        },
                    },
                    'minItems': 2,
                    'maxItems': 2,
                },
                'backend': 'check atom collisions',
            },  # fragment orientation

            'external symmetry': {
                'type': 'object',
                'description': 'The species external symmetry (excluding internal rotation symmetry',
                'required': ['value', 'method'],
                'properties': {
                    'value': {
                        'type': 'integer',
                        'description': 'The species external symmetry value (excluding internal rotation symmetry',
                        'examples': [2],
                    },
                    'method': {
                        'type': 'string',
                        'description': 'The method used to determine the external symmetry',
                        'examples': ['S. Patchkovskii brute force', 'author'],
                    },
                },
                'backend': 'compute and compare, flag for the reviewer',
            },  # external symmetry

            'chirality': {
                'type': 'object',
                'description': 'The chiral centers present in the species',
                'required': ['centers', 'types'],
                'properties': {
                    'centers': {
                        'type': 'array',
                        'description': 'Chiral atom centers',
                        'items': {
                            'type': 'array',
                            'description': '1-indexed atom indices',
                            'items': {
                                'type': 'number',
                            },
                            'minItems': 1,
                            'maxItems': 2,
                        },
                        'minItems': 1,
                    },
                    'types': {
                        'type': 'array',
                        'description': 'The respective chiral center types',
                        'items': {
                            'type': 'string',
                            'description': 'A chiral center type',
                            'enum': ['R', 'S', 'NR', 'NS', 'E', 'Z'],
                        },
                        'minItems': 1,
                    },
                },
                'backend': 'compute and compare, flag for the reviewer',
                'examples': [{'centers': [[1], [2], [5, 6]],
                              'types': ['R', 'NS', 'E']}],
            },  # chirality

            'conformation info': {
                'type': 'object',
                'description': 'Information relating to the conformer',
                'required': ['generator', 'is local well', 'is global minimum'],
                'properties': {
                    'generator': {
                        'type': 'string',
                        'description': 'The method used to generate the conformer(s)',
                    },
                    'is local well': {
                        'type': 'boolean',
                        'description': 'Whether this conformer represents a well at the opt level of theory',
                    },
                    'is global minimum': {
                        'type': 'boolean',
                        'description': 'Whether this conformer represents a global minimum '
                                       'to the best of the author knowledge',
                    },
                    'shift': {
                        'type': 'object',
                        'description': 'The bond distances, angles, and dihedrals which were modified relative to '
                                       'the global minimum conformer at the opt level of theory',
                        'required': ['atoms', 'values'],
                        'properties': geometry_parameters,
                    },
                },
                'backend': 'if global minimum, generate conformers to verify',
                'examples': [{'generator': 'ARC v 1.1.0',
                              'is local well': True,
                              'is global well': False,
                              'shift': {
                                  'atoms': [[2, 5], [5, 7, 9, 0]],
                                  'values': [1.75, 270.0],
                                  'units': ['angstroms', 'degrees'],
                              },
                              }],
            },  # conformation info

            # TS  # consult Steve, what's required for a barrierless TS?

            'is ts': {
                'type': 'boolean',
                'description': 'Whether this species represents a reaction transition state',
                'default': False,
            },  # is ts

            'irc trajectory': {
                'type': 'array',
                'description': 'The two IRC trajectories',
                'default': None,
                'items': {
                    'type': 'array',
                    'description': 'An IRC trajectory',
                    'items': {
                        'type': 'array',
                        'description': 'Cartesian coordinates of a point on the IRC trajectory',
                        'required': ['symbols', 'coords'],
                        'properties': cartesian_coordinates,
                    },
                },
                'minItems': 2,
                'maxItems': 2,
                'backend': 'count frames, alert the reviewer if too few',
            },  # irc trajectory

            # energy

            'electronic energy': {
                'type': 'object',
                'description': 'The species single point electronic energy (i.e., without zero-point energy correction)',
                'required': ['value', 'units'],
                'properties': {
                    'value': {
                        'type': 'number',
                    },
                    'units': {
                        'type': 'string',
                        'default': 'Hartree',
                    },
                },
                'examples': [{
                    'value': -253.145,
                    'units': 'Hartree',
                }],
            },  # electronic energy

            'E0': {
                'type': 'object',
                'description': 'The zero kelvin enthalpy (i.e., the electronic energy + zero-point energy correction)',
                'required': ['value', 'units'],
                'properties': {
                    'value': {
                        'type': 'number',
                    },
                    'units': {
                        'type': 'string',
                        'default': 'kJ/mol',
                    },
                },
                'examples': [
                    {
                        'value': -17.58,
                        'units': 'kJ/mol',
                    }
                ],
            },  # E0

            'electronic state': {
                'type': 'string',
                'description': 'The species electronic state',  # consult Bill and Steve
                'default': 'ground state',
                'backend': 'flag if not ground state',
            },  # electronic state

            'active space': {
                'type': 'object',
                'description': 'The active space used for a multireference calculation',
                'required': ['electrons', 'orbitals'],  # consult Steven
                'properties': {
                    'electrons': {
                        'type': 'integer',
                        'description': 'The number of electrons in the active space',
                    },
                    'orbitals': {
                        'type': 'integer',
                        'description': 'The number of orbitals in the active space',
                    },
                },
                'examples': {
                    'electrons': 5,
                    'orbitals': 10,
                },
                'backend': 'check electron/orbital ratio',
            },  # active space

            # modes

            'hessian': {
                'type': 'array',
                'description': 'The computed Hessian matrix',
                'items': 'number',
                'backend': 'check matrix dimensions',
            },  # hessian

            'unscaled unprojected frequencies': {
                'type': 'object',
                'description': 'The computed frequencies (automatically computed). '
                               'Note: Complex roots are represented by a negative number.',
                'required': ['value', 'units'],
                'properties': {
                    'value': {
                        'type': 'array',
                        'items': 'number',
                    },
                    'units': {
                        'type': 'string',
                        'default': 'cm^-1',
                    },
                    'model': {
                        'type': 'string',
                        'description': 'If this is an anharmonic oscillator, a model is required',
                    },
                    'arguments': {
                        'type': 'object',
                        'description': 'The anharmonic oscillator model arguments',
                    },
                },
                'examples': [
                    {
                        'value': [-500.0, 301.5, 1500.0],
                        'units': 'cm^-1',
                    }
                ],
                'backend': 'Compare to the expected number of frequencies, flag negative frequencies',
            },  # unscaled unprojected frequencies

            'scaled projected frequencies': {
                'type': 'object',
                'description': 'The scaled and projected frequencies (user input). '
                               'Note: Complex roots are represented by a negative number.',
                'required': ['value', 'units', 'elimination method'],
                'properties': {
                    'value': {
                        'type': 'array',
                        'items': 'number',
                    },
                    'units': {
                        'type': 'string',
                        'default': 'cm^-1',
                    },
                    'elimination method': {
                        'type': 'string',
                        'description': 'The method used to eliminate frequencies corresponding to torsional modes',
                        'enum': ['projection', 'inspection'],
                    },
                    'backend': 'verify using the respective scaling factor',
                },
                'default': None,
                'examples': [
                    {
                        'value': [-490.0, 295.47, 1470.0],
                        'units': 'cm^-1',
                    }
                ],
            },  # scaled projected frequencies

            'normal displacement modes': {
                'type': 'array',
                'description': 'The normal displacement modes (not a user input, derived from the Hessian)',
                'items': {
                    'type': 'array',
                    'items': {
                        'type': 'number',
                    },
                    'minItems': 3,
                    'maxItems': 3,
                },
                'backend': 'check dimensions',
            },  # normal displacement modes

            'rigid rotor': {
                'type': 'string',
                'description': 'The rigid rotor type (can be derived from the geometry and moments of inertia)',
                'enum': ['atom', 'linear', 'spherical top', 'symmetric top', 'asymmetric top'],
                'backend': 'verify atom/linear/non-linear-polyatomic, verify against moments of inertia',
            },  # rigid rotor

            'treatment': {
                'type': 'string',
                'description': 'The statistical mechanics treatment of the species',
                'enum': ['RRHO', 'RRAO'],  # consult Bill
                'default': 'RRHO',
                'backend': 'If this is an RRAO (anharmonic oscillator), check the respective arguments under '
                           '"unscaled unprojected frequencies"',
            },  # treatment

            'rotational constants': {
                'type': 'object',
                'description': 'Rotational constants (automatically computed)',
                'required': ['value', 'units'],
                'properties': {
                    'value': {
                        'type': 'array',
                        'description': 'Rotational constants values',
                        'items': 'number',
                        'minItems': 3,
                        'maxItems': 3,
                    },
                    'units': {
                        'type': 'string',
                        'description': 'Rotational constants units',
                    },
                },
                'examples': [{'value': [1.2, 5.6, 33.4],
                              'units': 'J/mol'}],
            },  # rotational constants

            'torsions': {
                'type': 'array',
                'description': 'The torsional modes',
                'items': {
                    'type': 'object',
                    'description': 'A torsional mode',
                    'required': ['computation type', 'dimension'],
                    'properties': {
                        'computation type': {
                            'type': 'string',
                            'description': 'The torsional mode computation type',
                            'enum': ['single point', 'constrained optimization', 'continuous constrained optimization'],
                        },
                        'dimension': {
                            'type': 'integer',
                            'description': 'The torsional mode dimension',
                            'minimum': 1,
                        },
                        'constraints': {
                            'type': 'object',
                            'description': 'Any constraints used in the optimization '
                                           'other than the primary torsion modes',
                            'required': ['atoms', 'values'],
                            'properties': geometry_parameters,
                        },
                        'symmetry': {
                            'type': 'integer',
                            'description': 'Internal rotation symmetry',
                        },
                        'treatment': {
                            'type': 'string',
                            'description': 'The torsion treatment method',
                            'enum': ['hindered rotor', 'free rotor', 'rigid top', 'hindered rotor density of states'],
                        },
                        'torsion': {
                            'type': 'array',
                            'description': 'The atoms describing in the torsion. '
                                           'The number of entries must be equal to the torsion dimension',
                            'items': {
                                'type': 'array',
                                'description': 'The (1-indexed) atom indices describing the torsion',
                                'items': {
                                    'type': 'integer',
                                    'description': 'An atom index',
                                },
                                'minimum': 1,
                                'minItems': 4,
                                'maxItems': 4,
                            },
                        },
                        'top': {
                            'type': 'array',
                            'description': 'The atoms describing in the torsion. '
                                           'The number of entries must be equal to the torsion dimension',
                            'items': {
                                'type': 'integer',
                                'description': 'The (1-indexed) atom indices of all atoms on one side of the rotor, '
                                               'including (only) one pivotal atom',
                            },
                            'minimum': 1,
                            'minItems': 2,
                            'backend': 'only two shared indices with the torsion, flag if inconsistent with '
                                       'connectivity',
                        },
                        'energies': {
                            'type': 'array',
                            'description': 'The energies of the scan, an ND array',
                            'items': {
                                'type': 'array',
                                'description': 'A lower dimension energy scan',
                                'items': 'number',
                                'minimum': 0,
                            },
                        },
                        'dihedrals': {
                            'type': 'array',
                            'description': 'The dihedral angles of the scan, an ND array',
                            'items': {
                                'type': 'array',
                                'description': 'A lower dimension array of dihedral angles',
                                'items': 'number',
                                'minimum': -180,
                                'maximum': 180,
                            },
                        },
                    },
                },
                'default': None,
                'backend': 'generate torsions, flag missing ones',
            },  # torsions

            # thermodynamic data

            'H298': {
                'type': 'object',
                'description': 'The standard (298.15 K, 1 bar) enthalpy change of formation',
                'required': ['value', 'units'],
                'properties': {
                    'value': {
                        'type': 'number',
                    },
                    'units': {
                        'type': 'string',
                        'default': 'kJ/mol',
                    },
                },
                'backend': 'compare to other entries in the database, compare to GAV, compare to NIST',
                'examples': [{'value': -234.97, 'units': 'kJ/mol'}],
            },  # H298

            'S298': {
                'type': 'object',
                'description': 'The standard (298.15 K, 1 bar) entropy change of formation',
                'required': ['value', 'units'],
                'properties': {
                    'value': {
                        'type': 'number',
                    },
                    'units': {
                        'type': 'string',
                        'default': 'J/(mol*K)',
                    },
                },
                'backend': 'compare to other entries in the database, compare to GAV, compare to NIST',
                'examples': [{'value': 280.33, 'units': 'J/(mol*K)'}],
            },  # S298

            'Cp': {
                'type': 'object',
                'description': 'The constant pressure heat capacity',
                'required': ['value', 'temperatures', 'units'],
                'properties': {
                    'value': {
                        'type': 'array',
                        'description': 'The constant pressure heat capacity values',
                        'items': 'number',
                    },
                    'temperatures': {
                        'type': 'array',
                        'description': 'The temperatures in K corresponding to the above discrete '
                                       'constant pressure heat capacity values',
                        'items': 'number',
                        'minimum': 0,
                        'default': [300, 400, 500, 600, 800, 1000, 1500],
                    },
                    'units': {
                        'type': 'string',
                        'default': 'J/(mol*K)',
                    },
                },
                'backend': 'compare to other entries in the database, compare to GAV, compare to NIST',
                'examples': [{'value': [65.48, 81.13, 95.77, 108.41, 127.90, 142.67, 166.02],
                              'temperatures': [300, 400, 500, 600, 800, 1000, 1500],
                              'units': 'J/(mol*K)'}],
            },  # Cp

            'heat capacity model': {
                'type': 'object',
                'description': 'The Heat capacity model and coefficients',
                'required': ['model', 'T min', 'T max', 'coefficients'],
                'properties': {
                    'model': {
                        'type': 'string',
                        'description': 'The cheat capacity model',
                        'default': 'NASA',
                    },
                    'T min': {
                        'type': 'object',
                        'description': 'The minimum temperature range',
                        'required': ['value', 'units'],
                        'properties': {
                            'value': {
                                'type': 'number',
                            },
                            'units': {
                                'type': 'string',
                                'default': 'K',
                            },
                        },
                    },
                    'T max': {
                        'type': 'object',
                        'description': 'The maximum temperature range',
                        'required': ['value', 'units'],
                        'properties': {
                            'value': {
                                'type': 'number',
                            },
                            'units': {
                                'type': 'string',
                                'default': 'K',
                            },
                        },
                    },
                    'coefficients': {
                        'type': 'object',
                        'description': 'The coefficients of the heat capacity model',
                    },
                },
                'backend': 'compare to other entries in the database, compare to GAV, compare to NIST',
            },  # heat capacity model

            'energy corrections': {
                'type': 'object',
                'description': 'Energy corrections used to compute the thermodynamic properties',
                'properties': {
                    'AEC': {
                        'type': 'integer',
                        'description': 'An ID pointer to the relevant entry in the atom energy correction table',
                    },
                    'BAC': {
                        'type': 'integer',
                        'description': 'An ID pointer to the relevant entry in the bond additivity correction table',
                    },
                    'SOC': {
                        'type': 'integer',
                        'description': 'An ID pointer to the relevant entry in the '
                                       'spin-orbit interaction energy correction table',
                    },
                    'isodesmic reactions': {
                        'type': 'object',
                        'description': 'The isodesmic reactions used for the energy correction',
                        'properties': {
                            'level': {
                                'type': 'object',
                                'description': 'The level of theory used for calculating the standard enthalpy change '
                                               'of the reactions',
                                'required': ['method'],
                                'properties': level_of_theory_properties,
                            },
                            'reactions': {
                                'type': 'array',
                                'description': 'ID pointers to the relevant entries in the '
                                               'isodesmic reactions correction table',
                                'items': {
                                    'type': 'integer',
                                    'description': 'An ID pointer to a relevant entry in the '
                                                   'isodesmic reactions correction table',
                                },
                            },
                        },
                    },
                },
            },  # energy corrections

            'statmech software': {
                'type': 'string',
                'description': 'The statistical mechanics software and version '
                               'used for the thermodynamic properties computation',
            },  # statmech software

            'levels': {
                'type': 'object',
                'description': 'The levels of theory',
                'required': ['sp'],
                'properties': {
                    'opt': {
                        'type': 'object',
                        'description': 'The optimization level of theory',
                        'required': ['method'],
                        'properties': level_of_theory_properties,
                    },
                    'freq': {
                        'type': 'object',
                        'description': 'The frequencies calculation level of theory',
                        'required': ['method'],
                        'properties': level_of_theory_properties,
                    },
                    'scan': {
                        'type': 'object',
                        'description': 'The torsion scan calculation level of theory',
                        'required': ['method'],
                        'properties': level_of_theory_properties,
                    },
                    'irc': {
                        'type': 'object',
                        'description': 'The IRC calculation level of theory',
                        'required': ['method'],
                        'properties': level_of_theory_properties,
                    },
                    'sp': {
                        'type': 'object',
                        'description': 'The single point energy calculation level of theory',
                        'required': ['method'],
                        'properties': level_of_theory_properties,
                    },
                },
                'backend': 'check against Basis Set Exchange',
                'examples': [{'opt': {'method': 'wb97xd', 'basis': 'def2tzvp', 'dispersion': ''},
                              'freq': {'method': 'wb97xd', 'basis': 'def2tzvp', 'dispersion': ''},
                              'scan': {'method': 'wb97xd', 'basis': 'def2tzvp', 'dispersion': ''},
                              'irc': None,
                              'sp': {'method': 'DLPNO-CCSD(T)-F12', 'basis': 'cc-pVTZ-F12',
                                     'auxiliary_basis': 'aug-cc-pVTZ/C cc-pVTZ-F12-CABS'},
                              }]
            },  # levels

            'ess': {
                'type': 'object',
                'description': 'The electronic structure software used for the different computations',
                'required': ['sp'],
                'properties': {
                    'opt': {
                        'type': 'object',
                        'description': 'The electronic structure software used for the optimization',
                        'required': ['software', 'version'],
                        'properties': ess_properties,
                        },
                    'freq': {
                        'type': 'object',
                        'description': 'The electronic structure software used for the frequencies calculation',
                        'required': ['software', 'version'],
                        'properties': ess_properties,
                        },
                    'scan': {
                        'type': 'object',
                        'description': 'The electronic structure software used for the torsion scan calculation',
                        'required': ['software', 'version'],
                        'properties': ess_properties,
                        },
                    'irc': {
                        'type': 'object',
                        'description': 'The electronic structure software used for the IRC calculation',
                        'required': ['software', 'version'],
                        'properties': ess_properties,
                        },
                    'sp': {
                        'type': 'object',
                        'description': 'The electronic structure software used for the single point energy calculation',
                        'required': ['software', 'version'],
                        'properties': ess_properties,
                    },
                },
                'examples': [{'opt': {'software': 'Psi4', 'version': 'v1.3.2'},
                              'freq': {'software': 'Psi4', 'version': 'v1.3.2'},
                              'scan': {'software': 'Gaussian', 'version': '16 ES64L-G16RevB.01'},
                              'irc': None,
                              'sp': {'software': 'Orca', 'version': '4.2.1'},
                              }],
            },  # ess

            'files': {
                'type': 'object',  # consult Matt: how to store files? paths?
                'description': 'The output files of the electronic structure computations',
                'required': ['sp'],
                'properties': {
                    'opt': {
                        'type': 'string',
                        'description': 'The path to the optimization output file',
                    },
                    'freq': {
                        'type': 'string',
                        'description': 'The path to the frequencies calculation output file',
                    },
                    'scan': {
                        'type': 'array',
                        'description': 'The path to the torsion scan calculation output files',
                        'items': {
                            'type': 'object',
                            'description': 'A path to a torsion scan calculation output file',
                            'properties': {
                                'torsions': {
                                    'type': 'array',
                                    'description': 'The 1-indexed torsion atom indices',
                                    'items': {
                                        'type': 'integer',
                                        'description': 'An atom index',
                                    },
                                    'minimum': 1,
                                },
                                'path': {
                                    'type': 'string',
                                    'description': 'A path to a torsion scan calculation output file',
                                },
                            },
                        },
                    },
                    'irc': {
                        'type': 'array',
                        'description': 'The path to the IRC calculation output files',
                        'items': {
                            'type': 'string',
                            'description': 'A path to an IRC calculation output file',
                        },
                        'minItems': 2,
                        'maxItems': 2,
                    },
                    'sp': {
                        'type': 'string',
                        'description': 'The path to the single point energy output file',
                    },
                },
                'backend': 'check the computation type and number of files correspond to expectation. '
                           'parse geometry, frequencies and compare',
                'examples': [{'opt': 'path/to/file.out',
                              'freq': 'path/to/file.out',
                              'scan': [{'torsion': [1, 2, 3, 4], 'path': 'path/to/file.out'},
                                       {'torsion': [5, 8, 9, 6], 'path': 'path/to/file.out'}],
                              'irc': ['path/to/file.out', 'path/to/file.out'],
                              'sp': 'path/to/file.out',
                              }],
            },

        },  # species properties

    }  # species

    return species


@app.get("/reaction/")
async def reaction():
    """Defines a reaction schema"""

    reaction = {
        '$schema': 'http://json-schema.org/schema#',
        '$id': 'https://github.com/tckdb/TCKDB',
        'title': 'Reaction',
        'type': 'object',
        'description': 'The Reaction schema',
        'required': ['proveance',
                     ],

        'properties': {

            # general

            'id': {
                'type': 'integer',
                'description': 'The TCKDB unique reaction identifier (automatically assigned)',
            },  # id

            'proveance': proveance,

            'label': {
                'type': 'string',
                'description': 'A free user label for the reaction',
                'default': None,
                'examples': ['CH3OH + H <=> CH3O + H2'],
                'backend': 'no line breaks, arrow and "+" validity, compare to reported reaction order',
            },  # label

            'review': review,

            'literature': {
                'type': 'integer',
                'description': 'A literature ID (can be amended)',
                'backend': 'check validity, map author vs. object for the reviewer',
            },  # literature

            'retracted': {
                'type': 'object',
                'description': 'Whether this object was retracted (can be amended)',
                'properties': {
                    'retracted': {
                        'type': 'boolean',
                        'default': 'False',
                    },
                    'reason': {
                        'type': 'string',
                        'description': 'A reason for retracting this object',
                    },
                },
                'default': {'retracted': False},
            },  # retracted

            'extras': {
                'type': 'object',
                'description': 'Any additional information in the form of a Python dictionary',
            },  # extras

            # chemistry




        },
    }

    return reaction

# for a reaction object add: "rate theory" (TST, RRKM, VTST, VEC-TST), tunneling (Eckart, Wigner)
# for network add ME method (MSC, RS, CSE)
# atom map? # consult
# perceive wells from IRC and compare


@app.get("/network/")
async def network():
    """Defines a network schema"""

    reaction = {
        '$schema': 'http://json-schema.org/schema#',
        '$id': 'https://github.com/tckdb/TCKDB',
        'title': 'Network',
        'type': 'object',
        'description': 'The Network schema',
        'required': [
                     ],

        'properties': {},
    }

    return reaction


@app.get("/energy_transfer_model/")
async def energy_transfer_model():
    """Defines an energy_transfer_model schema"""

    energy_transfer_model = {
        '$schema': 'http://json-schema.org/schema#',
        '$id': 'https://github.com/tckdb/TCKDB',
        'title': 'Energy Transfer Model',
        'type': 'object',
        'required': ['model', 'parameters'],
        'description': 'The species energy transfer model during collisions',
        'properties': {
            'model': {
                'type': 'string',
                'description': 'The model used for determining the energy transfer',
            },
            'parameters': {
                'type': 'object',
                'description': 'The energy transfer model parameters',
            },
        },
        'examples': [{
            'model': 'Single Exponential Down',
            'parameters': {
                'alpha0': {
                    'value': 175,
                    'units': 'cm^-1'
                },
                'T0': {
                    'value': 300,
                    'units': 'K'
                },
                'n': '0.52',
            },
        }],
    }

    return energy_transfer_model


@app.get("/L-J/")
async def lennard_jones():
    """Defines an L-J schema"""

    lennard_jones = {
        '$schema': 'http://json-schema.org/schema#',
        '$id': 'https://github.com/tckdb/TCKDB',
        'title': 'Lennard_jones coefficients',
        'type': 'object',
        'required': ['sigma', 'epsilon'],
        'description': 'The species Lennard-Jones sigma and epsilon coefficients',
        'properties': {
            'sigma': {
                'type': 'object',
                'description': 'The distance at which the inter-particle potential is zero',
                'required': ['value', 'units'],
                'properties': {
                    'value': {
                        'type': 'number',
                    },
                    'units': {
                        'type': 'string',
                    },
                },
            },
            'epsilon': {
                'type': 'object',
                'description': 'The Lennard-Jones potential well depth',
                'required': ['value', 'units'],
                'properties': {
                    'value': {
                        'type': 'number',
                    },
                    'units': {
                        'type': 'string',
                    },
                },
            },
        },
        'examples': [{
            'sigma': {
                'value': 4.467,
                'units': 'angstrom',
            },
            'epsilon': {
                'value': 387.557,
                'units': 'K',
            },
        }],
    }

    return lennard_jones


@app.get("/freq_scaling/")
async def freq_scaling():
    """Defines a frequency scaling factor schema"""

    freq_scaling = {
        '$schema': 'http://json-schema.org/schema#',
        '$id': 'https://github.com/tckdb/TCKDB',
        'title': 'Frequency scaling factor',
        'type': 'object',
        'required': ['level', 'factor', 'source'],
        'description': 'The frequency scaling factor corresponding to the specified level of theory',
        'properties': {
            'level': {
                'type': 'object',
                'description': 'The level of theory',
                'required': ['method'],
                'properties': level_of_theory_properties,
            },
            'factor': {
                'type': 'number',
                'description': 'The frequency scaling factor',
                'minimum': 0,
            },
            'source': {
                'type': ['object', 'string'],
                'description': 'The source for the frequency scaling factor (either a reference object, '
                               'or the method used to calculate it)',
            },
        },
    }

    return freq_scaling



#
# # have energy corrections database, amendable (more atoms and bonds per entry), have unique IDs to refer to
#
#
#         'AEC': {  # these are amendable
#             'type': 'array',
#             # Ask Matt: Python dict {'C': 3, 'H': 1} represented as a list of objects, not a single "object"/dict
#             # Ask Matt: MAY vary by species, but shouldn't be replicated in the DB per species...
#             'description': 'Atom energy corrections',
#             'items': {
#                 'type': 'object',
#                 'description': 'An atom energy correction for an atom',
#                 'required': ['atom', 'correction'],
#                 'properties': {
#                     'atom': {
#                         'type': 'object',
#                         'description': 'The atom descriptor',
#                         'required': ['symbol'],
#                         'properties': {
#                             'symbol': {
#                                 'type': 'string',
#                                 'description': 'The element symbol'
#                             },
#                             'isotope': {
#                                 'type': 'integer',
#                                 'description': 'The isotope mass number'
#                             },
#                         },
#                     },
#                     'correction': {
#                         'type': 'object',
#                         'description': 'The corresponding atom energy correction',
#                         'required': ['value', 'units'],
#                         'properties': {
#                             'value': {
#                                 'type': 'number',
#                             },
#                             'units': {
#                                 'type': 'string',
#                             },
#                         },
#                     },
#                 },
#                 'examples': [
#                     [{'atom': {'symbol': 'C', 'isotope': 13},
#                       'correction': {'value': -37.842468, 'units': 'Hartree'}},
#                      {'atom': {'symbol': 'H'},
#                       'correction': {'value': -0.499818, 'units': 'Hartree'}},
#                      ]
#                 ],
#             },
#         },  # AEC
#
#         'SOC': {
#             'type': 'array',
#             'description': 'Spin-orbit interaction energy correction',
#             'items': {
#                 'type': 'object',
#                 'description': 'A spin-orbit interaction energy correction for an atom',
#                 'required': ['atom', 'correction'],
#                 'properties': {
#                     'atom': {
#                         'type': 'object',
#                         'description': 'The atom descriptor',
#                         'required': ['symbol'],
#                         'properties': {
#                             'symbol': {
#                                 'type': 'string',
#                                 'description': 'The element symbol'
#                             },
#                             'isotope': {
#                                 'type': 'integer',
#                                 'description': 'The isotope mass number'
#                             },
#                         },
#                     },
#                     'correction': {
#                         'type': 'object',
#                         'description': 'The corresponding spin-orbit interaction energy correction',
#                         'required': ['value', 'units'],
#                         'properties': {
#                             'value': {
#                                 'type': 'number',
#                             },
#                             'units': {
#                                 'type': 'string',
#                             },
#                         },
#                     },
#                 },
#             },
#         },  # SOC
#
#         'BAC': {
#             'type': 'object',
#             'description': 'Bond additivity energy corrections',
#             'properties': {
#                 'BAC type': {
#                     'type': 'string',
#                     'description': 'The BAC type',
#                     'enum': ['Petersson', 'Melius'],
#                 },
#                 # todo: BAC data  - add isodesmic rxn, # which source was used for each parameter
#             },
#         },  # BAC
