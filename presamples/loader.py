from .array import IrregularPresamplesArray
from .errors import IncompatibleIndices, ConflictingLabels
from .indexer import Indexer
from .package_interface import ParametersMapping
from .utils import validate_presamples_dirpath
from bw2calc.indexing import index_with_arrays
from bw2calc.matrices import TechnosphereBiosphereMatrixBuilder as MB
from bw2calc.utils import md5
from pathlib import Path
import itertools
import json
import numpy as np
import os
import wrapt


@wrapt.decorator
def nonempty(wrapped, instance, args, kwargs):
    """Skip function execution if there are no presamples"""
    if instance.empty:
        return
    else:
        return wrapped(*args, **kwargs)


class PackagesDataLoader:
    """Efficiently map presampled arrays and insert their values into LCA matrices.

    The presamples directory will contain the following two files for each resource:

    * ``{uuid}.{data package index}.samples.npy``: Array of samples values in `npy format <https://docs.scipy.org/doc/numpy-dev/neps/npy-format.html>`__.
    * ``{uuid}.{data package index}.indices.npy``: A Numpy `record array <https://docs.scipy.org/doc/numpy/reference/generated/numpy.recarray.html>`__ in ``npy`` format with something like the following columns:
        * ``input``: uint32
        * ``output``: uint32
        * ``row``: uint32
        * ``col``: uint32
        * ``type``: uint8

    The columns above are for technosphere and biosphere samples, there can be small modifications for other types of samples. For example, characterization factors would normally have the columns "flow" and "row".

    For technosphere samples, ``type`` is an integer from ``bw2data.utils.TYPE_DICTIONARY`` that indicates whether the modified exchange is an consumer or producer.

    The metadata is loaded and concatenated. Input and output values for LCA matrix elements are mapped to the correct row and column indices.

    The samples arrays are not loaded into memory, but memory mapped to reduce resource consumption. As such, they are not concatenated.

    Normal life cycle for this class:

    * Instantiate with some directory paths and (optionally) a random seed; load data.
    * After building an LCA object, map the index arrays with ``index_arrays`` from integer ids to matrix rows and columns.
    * Before a static calculation, or for each Monte Carlo iteration, update the relevant matrices using the ``update_matrices`` function.

    Note that we currently assume that all index values will be present in the built matrix. Silent errors or losses in efficiency could happen if this assumption does not hold!

    ``seed``: Seed value to use for index RNGs. Default is to use the seed values in each package, only specify this if you want to override the default.

    """
    def __init__(self, dirpaths, seed=None):
        self.seed = seed

        self.data, self.parameter_metadata = [], []
        self.sample_indexers, self.msi, self.psi = [], [], []

        for dirpath in (dirpaths or []):
            validate_presamples_dirpath(Path(dirpath))
            # Even empty presamples have name and id
            section = self.load_data(Path(dirpath), self.seed)
            self.sample_indexers.append(section['indexer'])
            if section["matrix-data"]:
                self.data.append(section)
                self.msi.append(section['indexer'])
            if section['parameter-metadata']:
                self.parameter_metadata.append(section['parameter-metadata'])
                self.psi.append(section['indexer'])

        self.empty = not bool(self.data)

    def __str__(self):
        return "PackagesDataLoader with {} resources".format(
            len(self.data))

    def __len__(self):
        return len(self.data)

    @classmethod
    def load_data(cls, dirpath, seed=None):
        """Load data and metadata from a directory.

        This function will consolidate presamples with the same type. We check to make sure the relevant metadata (e.g. row and column labels) is identical when doing such consolidation.

        Will also instantiate ``IrregularPresamplesArray`` objects.

        Returns a dictionary with a list of resources:

        ..code-block:: python

            {
                'name': name,
                'id': uuid,
                'resources': [{
                    'type': string,
                    'samples': IrregularPresamplesArray instance,
                    'indices': Numpy array,
                    'matrix': string,
                    "row from label": string,
                    "row to label": string,
                    "row dict": string,
                    "col from label": string,
                    "col to label": string,
                    "col dict": string,
                }]
            }

        """
        metadata = json.load(
            open(dirpath / "datapackage.json"),
            encoding="utf-8"
        )
        get_seed = lambda x: seed if seed is not None else x
        data = {
            'name': metadata['name'],
            'id': metadata['id'],
            'seed': metadata['seed'],
            'matrix-data': [],
            'parameter-metadata': None,
            'indexer': Indexer(get_seed(metadata['seed']))
        }
        resources = [obj for obj in metadata["resources"] if obj.get('matrix')]
        fltr = lambda x: x['type']
        resources.sort(key=fltr)
        for key, group in itertools.groupby(resources, fltr):
            group = cls.consolidate(dirpath, list(group))
            data['matrix-data'].append(group)

        parameter_resources = [
            obj for obj in metadata['resources'] if obj.get('names')
        ]
        if parameter_resources:
            data['parameter-metadata'] = {
                'path': dirpath,
                'resources': parameter_resources,
                'package_name': metadata['name']
            }

        return data

    @staticmethod
    def consolidate(dirpath, group):
        """Add together indices and samples in the same presamples directory if they have the same type.

        Consolidating is not necessary for the functionality of this class, but it does make it easier to do things like sensitivity analysis afterwards."""
        # Check that metadata is the same
        assert len({el['matrix'] for el in group}) == 1, "Conflicting matrices"
        if not len({
                    (el['row from label'], el['row to label'], el['row dict'])
                    for el in group
                }) == 1:
            raise ConflictingLabels
        if any(['col dict' in o for o in group]):
            if not len({
                        (el['col from label'], el['col to label'], el['col dict'])
                        for el in group
                    }) == 1:
                raise ConflictingLabels

        indices = [np.load(dirpath / r['indices']['filepath']) for r in group]
        # Check that indices have right shape
        if not len({o.dtype for o in indices}) == 1:
            raise IncompatibleIndices
        indices = np.hstack(indices)
        samples = IrregularPresamplesArray([
            (dirpath / el['samples']['filepath'], el['samples']['shape'])
            for el in group
        ])

        SKIP = ('indices', 'samples', 'profile', 'format', 'mediatype')
        result = {k: v for k, v in group[0].items() if k not in SKIP}
        result.update({'samples': samples, 'indices': indices})
        return result

    @nonempty
    def index_arrays(self, lca):
        """Add row and column values to the indices.

        As this function can be called multiple times, we check for each element if it has already been called, and whether the required mapping dictionary is present."""
        for obj in self.data:
            for elem in obj["matrix-data"]:
                # Allow for iterative indexing, starting with inventory
                if elem.get('indexed'):
                    # Already indexed
                    continue
                elif not hasattr(lca, elem['row dict']):
                    # This dictionary not yet built
                    continue
                elif "col dict" in elem and not hasattr(lca, elem['col dict']):
                    # This dictionary not yet built
                    continue

                index_with_arrays(
                    elem['indices'][elem['row from label']],
                    elem['indices'][elem['row to label']],
                    getattr(lca, elem['row dict'])
                )
                if "col dict" in elem:
                    index_with_arrays(
                        elem['indices'][elem['col from label']],
                        elem['indices'][elem['col to label']],
                        getattr(lca, elem['col dict'])
                    )
                elem['indexed'] = True

    @nonempty
    def update_matrices(self, lca, matrices=None):
        for indexer, obj in zip(self.msi, self.data):
            for elem in obj["matrix-data"]:
                try:
                    matrix = getattr(lca, elem['matrix'])
                except AttributeError:
                    # This LCA doesn't have this matrix
                    continue

                if matrices is not None and elem['matrix'] not in matrices:
                    continue

                sample = elem['samples'].sample(indexer.index)
                if elem['type'] == 'technosphere':
                    MB.fix_supply_use(elem['indices'], sample)
                if 'col dict' in elem:
                    matrix[
                        elem['indices'][elem['row to label']],
                        elem['indices'][elem['col to label']],
                    ] = sample
                else:
                    matrix[
                        elem['indices'][elem['row to label']],
                        elem['indices'][elem['row to label']],
                    ] = sample

    def update_sample_indices(self):
        for indexer in self.sample_indexers:
            next(indexer)

        if hasattr(self, "_parameters"):
            for i, o in zip(self.psi, self.parameters):
                o.index = i

    def parameters(self):
        if not hasattr(self, "_parameters"):
            self._parameters = [
                ParametersMapping(**metadata)
                for metadata in self.parameter_metadata
            ]
        return self._parameters