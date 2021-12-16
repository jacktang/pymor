# This file is part of the pyMOR project (https://www.pymor.org).
# Copyright 2013-2021 pyMOR developers and contributors. All rights reserved.
# License: BSD 2-Clause License (https://opensource.org/licenses/BSD-2-Clause)
import os
import pytest

from pymor.core.config import config, _PACKAGES


@pytest.mark.parametrize('pkg', _PACKAGES.keys())
@pytest.mark.skipif(condition=not os.environ.get('DOCKER_PYMOR', False),
                    reason='Guarantee only valid in the docker container')
def test_config(pkg):
    if pkg == 'DEALII':
        pytest.xfail('docker image setup for deal.II currently broken')
    assert getattr(config, f'HAVE_{pkg}')
