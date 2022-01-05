#!/usr/bin/env python3

import itertools
import json
import operator
import os
import sys
from contextlib import contextmanager
from functools import reduce
from pathlib import Path
from subprocess import check_output, CalledProcessError
import jinja2
import logging

REQUIRED_PLATFORMS = ('osx-64', 'linux-64', 'win-64')
# stars are not actually a glob pattern, but as-is in the conda search output
REQUIRED_PYTHONS = ('3.8.*', '3.9.*')
ANY_PYTHON_VERSION = 'any_python'
NO_PYTHON_VERSION = -1

ENV_TPL = r'''# THIS FILE IS AUTOGENERATED -- DO NOT EDIT #
name: pyMOR-ci
channels:
  - conda-forge
dependencies:
  - anaconda-client
  - conda-build
  - pip
{% for pkg in available %}
  - {{pkg}}
{%- endfor %}

  - pip:
    - -r ../requirements-ci.txt
# THIS FILE IS AUTOGENERATED -- DO NOT EDIT #

'''

BLOCKLIST = tuple()
PYPI_TO_CONDA_PACKAGENAME_MAPPING = {'torch': 'pytorch-cpu'}
NO_ARCH = 'noarch'
THIS_DIR = Path(__file__).resolve().parent
LOGFILE = THIS_DIR / 'create_conda_env.log'

logging.basicConfig(filename=LOGFILE, level=logging.WARNING, filemode='wt')


@contextmanager
def change_to_directory(name):
    """Change current working directory to `name` for the scope of the context."""
    old_cwd = os.getcwd()
    try:
        yield os.chdir(name)
    finally:
        os.chdir(old_cwd)


def _parse_req_file(path):
    path = Path(path).resolve()
    assert path.exists()
    assert path.is_file()
    pkgs = []
    with change_to_directory(path.parent):
        for line in open(path, 'rt').readlines():
            line = line.strip()
            if line.startswith('-r'):
                pkgs += _parse_req_file(line[line.find('-r ')+3:])
                continue
            if line.startswith('#'):
                continue
            if ';' in line:
                dropped = line.split(';')[0]
                logging.debug(f'Dropping chained specifier, using {dropped} instead of {line}')
                line = dropped
            name_only = _strip_markers(line)
            if name_only in BLOCKLIST:
                continue
            if name_only in PYPI_TO_CONDA_PACKAGENAME_MAPPING.keys():
                line = line.replace(name_only, PYPI_TO_CONDA_PACKAGENAME_MAPPING[name_only])
            pkgs.append(line)
    return pkgs


def _strip_markers(name):
    for m in '!;<>=':
        try:
            i = name.index(m)
            name = name[:i].strip()
        except ValueError:
            continue
    return name


def _search_single(pkg, plat):
    """Search needs to explicitly say its subdir, else only the host's native is searched"""
    cmd = ['/usr/bin/env', 'conda', 'search', '--channel=conda-forge', '--json', f'{pkg}[subdir={plat}]']
    try:
        output = check_output(cmd)
    except CalledProcessError as e:
        if plat != NO_ARCH:
            logging.debug(f'Falling back to noarch for {pkg} - {plat}')
            return _search_single(pkg, NO_ARCH)
        try:
            err = json.loads(e.output)['error']
            if 'PackagesNotFoundError' in err:
                return None, []
            raise RuntimeError(err)
        except Exception:
            raise e

    pkg_name = _strip_markers(pkg).lower()
    out = json.loads(output)
    ll = list(itertools.chain.from_iterable((data for name, data in out.items() if name == pkg_name)))
    return plat, list(reversed(ll))


def _extract_conda_py(release):
    try:
        if release['package_type'] == 'noarch_python':
            return ANY_PYTHON_VERSION
    except KeyError:
        pass
    for pkg in release['depends']:
        if pkg.startswith('python_abi'):
            # format 'python_abi 3.9.* *_cp39'
            return pkg.split(' ')[1]
        if pkg.startswith('python'):
            # format ''python >=3.9,<3.10.0a0''
            l, r = pkg.find('>='), pkg.find(',')
            return pkg[l+2:r]+'.*'
    return NO_PYTHON_VERSION


def _available_on_required(json_result, required_plats, required_pys):
    required_tuples = list(itertools.product(required_plats, required_pys))
    name = 'PackageNameNotSet'
    for release in json_result:
        plat = release['subdir']
        if plat not in required_plats and plat != NO_ARCH:
            continue
        py = _extract_conda_py(release)
        if py in required_pys or py == ANY_PYTHON_VERSION:
            covered_pys = [py] if py != ANY_PYTHON_VERSION else required_pys
            covered_plats = [plat] if plat != NO_ARCH else required_plats
            to_remove = itertools.product(covered_plats, covered_pys)
            for pair in to_remove:
                try:
                    # combinations can be found multiple times
                    required_tuples.remove(pair)
                except ValueError as e:
                    if 'list.remove' in str(e):
                        continue
                    raise e
        if len(required_tuples) == 0:
            return True
        name = release['name']
    logging.error(f'{name} not available on {required_tuples}')
    return False


def _search(pkg):
    """If a resul is noarch, we can return early"""
    for plat in REQUIRED_PLATFORMS:
        found_plat, json_list = _search_single(pkg, plat)
        yield json_list
        if found_plat == NO_ARCH:
            return


def main(input_paths, output_path='conda-environment.yml'):
    available = []
    wanted = set(reduce(operator.concat, (_parse_req_file(p) for p in input_paths)))
    for pkg in wanted:
        data = reduce(operator.concat, _search(pkg))
        if _available_on_required(json_result=data,
                                  required_plats=REQUIRED_PLATFORMS,
                                  required_pys=REQUIRED_PYTHONS):
            available.append(pkg)
    for a in available:
        wanted.remove(a)
    available, wanted = sorted(list(available)), sorted(list(wanted))
    tpl = jinja2.Template(ENV_TPL)
    with open(output_path, 'wt') as yml:
        yml.write(tpl.render(available=available))
    return available, wanted


if __name__ == '__main__':
    out_fn = THIS_DIR / 'conda-env.yml'
    available, wanted = main(sys.argv[1:], output_path=out_fn)
    from rich.console import Console
    from rich.table import Table

    table = Table("available", "wanted", title="Conda search result")
    for el in itertools.zip_longest(available, wanted, fillvalue=''):
        table.add_row(*el)
    console = Console()
    console.print(table)
    console.print(f'Details at {LOGFILE}')
