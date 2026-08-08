import pytest

DATASET_DIR = r"D:\Rotpunküchen\dataset"


@pytest.fixture
def dataset_dir():
    return DATASET_DIR
