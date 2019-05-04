
import unittest
import numpy as np
import pysm

import pytest
from astropy.tests.helper import assert_quantity_allclose


#@pytest.mark.parametrize("freq", [30, 100, 353])
@pytest.mark.parametrize("freq", [30])
@pytest.mark.parametrize("model", ["f1"])
def test_synchrotron_model(model, freq):

    synchrotron = pysm.preset_models(model, nside=64)

    model_number = 4
    synch = pysm.read_map(
        "pysm_2_test_data/check{}freef_{}p0_64.fits".format(model_number, freq),
        64,
        unit=pysm.units.uK_RJ,
        field=0,
    ).reshape((1, 1, -1))

    assert_quantity_allclose(
        synch, synchrotron.get_emission(freq << pysm.units.GHz), rtol=1e-5
    )
