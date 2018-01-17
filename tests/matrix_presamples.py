from bw2data import mapping
from bw2data.tests import bw2test
from bw_presamples import *
from pathlib import Path
from scipy.sparse import dok_matrix
import json
import numpy as np
import os
import pytest
import tempfile


class MockLCA:
    def __init__(self):
        self.matrix = dok_matrix((5, 5))
        for i in range(5):
             for j in range(5):
                 self.matrix[i, j] = 0
        self.row_dict = {x: 2 * x for x in range(5)}
        self.col_dict = {x: 3 * x for x in range(5)}


@pytest.fixture
@bw2test
def package():
    a = np.ones(shape=(3, 4)) * 100
    b = [(1, 1), (1, 2), (2, 3)]
    metadata = {
        'row from label': 'f1',
        'row to label': 'f3',
        'row dict': 'row_dict',
        'col from label': 'f2',
        'col to label': 'f4',
        'col dict': 'col_dict',
        'matrix': 'matrix'
    }
    frmt = lambda x: (x[0], x[1], x[0], x[1])
    dtype = [
        ('f1', np.uint32),
        ('f2', np.uint32),
        ('f3', np.uint32),
        ('f4', np.uint32),
    ]
    id_, dirpath = create_presamples_package(
        [(a, b, 'mock', dtype, frmt, metadata)],
    )
    return dirpath

def test_init(package):
    mp = MatrixPresamples([package])
    assert not mp.empty
    assert len(mp.data) == 1
    assert 'id' in mp.data[0]
    assert 'name' in mp.data[0]
    assert len(mp.data[0]['resources']) == 1
    resources = mp.data[0]['resources'][0]
    assert resources['type'] == 'mock'
    assert resources['matrix'] == 'matrix'
    for key in ('row from label', 'row to label', 'row dict',
                'col from label', 'col to label', 'col dict'):
        assert key in resources
    assert isinstance(resources['samples'], IrregularPresamplesArray)
    assert isinstance(resources['indices'], np.ndarray)

def test_update_matrices(package):
    mp = MatrixPresamples([package])
    lca = MockLCA()
    mp.update_matrices(lca)
    assert lca.matrix[1, 1] == 100
    assert lca.matrix[1, 2] == 100
    assert lca.matrix[2, 3] == 100
    assert lca.matrix.sum() == 300

def test_update_matrices_missing_matrix(package):
    class WrongLCA:
        def __init__(self):
            self.wrong = dok_matrix((5, 5))
            for i in range(5):
                 for j in range(5):
                     self.wrong[i, j] = 0
            self.row_dict = {x: 2 * x for x in range(5)}
            self.col_dict = {x: 3 * x for x in range(5)}

    mp = MatrixPresamples([package])
    lca = WrongLCA()
    mp.update_matrices(lca)
    assert not lca.wrong.sum()

@bw2test
def test_update_matrices_technosphere():
    mapping.add('ABCDEF')
    for x, y in enumerate('ABCDEF'):
        assert mapping[y] == x + 1

    # 0 is production, 3 is substitution
    t1 = [('A', 'A', 0), ('A', 'B', 1), ('B', 'C', 3)]
    t2 = np.arange(3).reshape((3, 1)) + 10
    _, dirpath = create_presamples_package([(t2, t1, 'technosphere')])

    class LCA:
        def __init__(self):
            self.technosphere_matrix = dok_matrix((5, 5))
            for i in range(5):
                 for j in range(5):
                     self.technosphere_matrix[i, j] = 0
            self._activity_dict = {x: x-1 for x in range(1, 7)}
            self._product_dict = self._activity_dict

    lca = LCA()
    mp = MatrixPresamples([dirpath])
    mp.index_arrays(lca)
    mp.update_matrices(lca)
    assert lca.technosphere_matrix[0, 0] == 10
    assert lca.technosphere_matrix[0, 1] == -11
    assert lca.technosphere_matrix[1, 2] == 12
    assert lca.technosphere_matrix.sum() == 10 - 11 + 12

def test_update_matrices_one_dimensional():
    mapping.add('ABCDEF')
    for x, y in enumerate('ABCDEF'):
        assert mapping[y] == x + 1

    # 0 is production, 3 is substitution
    t1 = list('ACD')
    t2 = np.arange(3).reshape((3, 1)) + 10
    _, dirpath = create_presamples_package([(t2, t1, 'cf')])

    class LCA:
        def __init__(self):
            self.characterization_matrix = dok_matrix((5, 5))
            for i in range(5):
                 for j in range(5):
                     self.characterization_matrix[i, j] = 0
            self._biosphere_dict = {x: x-1 for x in range(1, 7)}

    lca = LCA()
    mp = MatrixPresamples([dirpath])
    mp.index_arrays(lca)
    mp.update_matrices(lca)
    assert lca.characterization_matrix[0, 0] == 10
    assert lca.characterization_matrix[2, 2] == 11
    assert lca.characterization_matrix[3, 3] == 12
    assert lca.characterization_matrix.sum() == 10 + 11 + 12

def test_index_arrays(package):
    mp = MatrixPresamples([package])
    lca = MockLCA()
    assert 'indexed' not in mp.data[0]['resources'][0]
    mp.index_arrays(lca)
    expected = [(1, 1, 2, 3), (1, 2, 2, 6), (2, 3, 4, 9)]
    assert mp.data[0]['resources'][0]['indices'].tolist() == expected
    assert mp.data[0]['resources'][0]['indexed']

def test_index_arrays_already_indexed(package):
    mp = MatrixPresamples([package])
    lca = MockLCA()
    assert 'indexed' not in mp.data[0]['resources'][0]
    expected = [(1, 1, 1, 1), (1, 2, 1, 2), (2, 3, 2, 3)]
    assert mp.data[0]['resources'][0]['indices'].tolist() == expected
    mp.index_arrays(lca)
    expected = [(1, 1, 2, 3), (1, 2, 2, 6), (2, 3, 4, 9)]
    assert mp.data[0]['resources'][0]['indices'].tolist() == expected
    assert mp.data[0]['resources'][0]['indexed']
    lca.row_dict = {x: 0 for x in range(5)}
    lca.col_dict = {x: 0 for x in range(5)}
    mp.index_arrays(lca)
    assert mp.data[0]['resources'][0]['indices'].tolist() == expected

def test_index_arrays_missing_row_dict(package):
    mp = MatrixPresamples([package])
    lca = MockLCA()
    del lca.row_dict
    expected = [(1, 1, 1, 1), (1, 2, 1, 2), (2, 3, 2, 3)]
    assert mp.data[0]['resources'][0]['indices'].tolist() == expected
    mp.index_arrays(lca)

def test_index_arrays_missing_col_dict(package):
    mp = MatrixPresamples([package])
    lca = MockLCA()
    del lca.col_dict
    expected = [(1, 1, 1, 1), (1, 2, 1, 2), (2, 3, 2, 3)]
    assert mp.data[0]['resources'][0]['indices'].tolist() == expected
    mp.index_arrays(lca)

def test_functionality_with_empty():
    dirpath = Path(tempfile.mkdtemp())
    datapackage = {
        "name": "foo",
        "id": "one",
        "profile": "data-package",
        "resources": []
    }
    with open(dirpath / "datapackage.json", "w", encoding='utf-8') as f:
        json.dump(datapackage, f)
    mp = MatrixPresamples([dirpath])
    assert mp.empty
    mp.index_arrays(None)
    mp.update_matrices(None)

def test_validate_dirpath_missing_datapackage(package):
    os.unlink(package / "datapackage.json")
    with pytest.raises(AssertionError):
        MatrixPresamples([package])

def test_validate_dirpath_missing_samples(package):
    for fp in os.listdir(package):
        if "samples.npy" in fp:
            os.unlink(package / fp)
    with pytest.raises(AssertionError):
        MatrixPresamples([package])

def test_validate_dirpath_altered_samples(package):
    for fp in os.listdir(package):
        if "samples.npy" in fp:
            with open(package / fp, "w") as f:
                f.write("woops")
    with pytest.raises(AssertionError):
        MatrixPresamples([package])

def test_validate_dirpath_missing_indices(package):
    for fp in os.listdir(package):
        if "indices.npy" in fp:
            os.unlink(package / fp)
    with pytest.raises(AssertionError):
        MatrixPresamples([package])

def test_validate_dirpath_altered_indices(package):
    for fp in os.listdir(package):
        if "indices.npy" in fp:
            with open(package / fp, "w") as f:
                f.write("woops")
    with pytest.raises(AssertionError):
        MatrixPresamples([package])

@bw2test
def test_seed_functions():
    a = np.arange(12).reshape((3, 4))
    b = [(1, 1), (1, 2), (2, 3)]
    metadata = {
        'row from label': 'f1',
        'row to label': 'f3',
        'row dict': 'row_dict',
        'col from label': 'f2',
        'col to label': 'f4',
        'col dict': 'col_dict',
        'matrix': 'matrix'
    }
    frmt = lambda x: (x[0], x[1], x[0], x[1])
    dtype = [
        ('f1', np.uint32),
        ('f2', np.uint32),
        ('f3', np.uint32),
        ('f4', np.uint32),
    ]
    _, dirpath = create_presamples_package(
        [(a, b, 'mock', dtype, frmt, metadata)],
    )
    mp = MatrixPresamples([dirpath], 987654321)
    sampler = mp.data[0]['resources'][0]['samples']
    first = [sampler.sample().sum() for _ in range(100)]
    mp = MatrixPresamples([dirpath], 987654321)
    sampler = mp.data[0]['resources'][0]['samples']
    second = [sampler.sample().sum() for _ in range(100)]
    assert first == second

    mp = MatrixPresamples([dirpath], 12345)
    sampler = mp.data[0]['resources'][0]['samples']
    third = [sampler.sample().sum() for _ in range(100)]
    assert first != third


@pytest.fixture
def mp():
    class Mock(MatrixPresamples):
        def __init__(self):
            pass

    return Mock()

def test_consolidate_mismatched_matrices(package, mp):
    group = [{'matrix': 'a'}, {'matrix': 'b'}]
    with pytest.raises(AssertionError):
        mp.consolidate(package, None, group)

def test_consolidate_conficting_row_labels(package, mp):
    pass

def test_consolidate_conflicting_col_labels(package, mp):
    pass

def test_consolidate_conflicting_indices(package, mp):
    pass

def test_consolidate(package, mp):
    pass

def test_consolidate_multiple_groups():
    pass