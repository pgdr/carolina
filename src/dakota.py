# Copyright 2013 National Renewable Energy Laboratory (NREL)
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.
#
# ++==++==++==++==++==++==++==++==++==++==
"""
Generic DAKOTA driver.
This uses the standard version of DAKOTA as a library (libdakota_src.so).

:class:`DakotaInput` holds DAKOTA input strings and can write them to a file.

:meth:`run_dakota` optionally accepts a boost.mpi MPI communicator to use.

:meth:`dakota_callback` can be invoked by DAKOTA's Python interface to
evaluate the model.

There is no other information passed to DAKOTA, so DAKOTA otherwise acts like
the command line version, in particular, all other inputs go through the input
file (typically generated by :class:`DakotaInput`).

:class:`DakotaBase` ties these together for a basic 'driver'.
"""

from __future__ import with_statement

import logging
import os
import sys
import weakref

# Needed to avoid a problem with 'locale::facet::_S_create_c_locale'
if sys.platform in ('cygwin', 'win32'):
    os.environ['LC_ALL'] = 'C'

import pyDAKOTA
import pyDAKOTA
from mpi4py import MPI
comm = MPI.COMM_WORLD

# Hard-coded assumption regarding availability of MPI.
if sys.platform in ('cygwin', 'win32'):
    _HAVE_MPI = False
else:
    _HAVE_MPI = True

# Dictionary to map from str(id(data)) to data object.
_USER_DATA = weakref.WeakValueDictionary()


class DakotaBase(object):
    """ Base class for a DAKOTA 'driver'. """

    def __init__(self):
        self.input = DakotaInput()

    def run_dakota(self, infile='dakota.in', mpi_comm=None, use_mpi=True,
                   stdout=None, stderr=None):
        """
        Write `self.input` to `infile`, providing `self` as `data`.
        Then run DAKOTA with that input, MPI specification, and files.
        DAKOTA will then call our :meth:`dakota_callback` during the run.
        """
        if comm.Get_rank() == 0: 
            self.input.write_input(infile, data=self)
            run_dakota(infile, mpi_comm, use_mpi, stdout, stderr)

    def dakota_callback(self, **kwargs):
        """ Invoked from global :meth:`dakota_callback`, must be overridden. """
        raise NotImplementedError('dakota_callback')


class DakotaInput(object):
    """
    Simple mechanism where we store the strings that will go in each section
    of the DAKOTA input file.  The ``interface`` section defaults to a
    configuration that will use :meth:`dakota_callback`, assuming a driver
    object is passed as `data` to :meth:`write`.

        # Provide your own input with key word arguments,
        # e.g.: DakotaInput(method=["multidim_parameter_study",
        #                           "partitions = %d %d" % (nx, nx)])

    """
    def __init__(self, **kwargs):
        self.environment = [
            "tabular_graphics_data",
        ]
        self.method = [
            "multidim_parameter_study",
            "  partitions = 4 4",
        ]
        self.model = [
            "single",
        ]
        self.variables = [
            "continuous_design = 2",
            "  lower_bounds    3    5",
            "  upper_bounds    4    6",
            "  descriptors   'x1' 'x2'",
        ]
        self.interface = [
        #    "python asynchronous evaluation_concurrency = %i" % comm.Get_size(),
            "deactivate evaluation_cache",
            "python",
            "  numpy",
            "  analysis_drivers = 'dakota:dakota_callback'",
        ]
        self.responses = [
            "num_objective_functions = 1",
            "no_gradients",
            "no_hessians",
        ]

        for key in kwargs:
            setattr(self, key, kwargs[key])

    def write_input(self, infile, data=None):
        """
        Write input file sections in standard order.
        If data is not None, its id is written to ``analysis_components``.
        The invoked Python method should then recover the original object
        using :meth:`fetch_data`.
        """
        with open(infile, 'w') as out:
            for section in ('environment', 'method', 'model', 'variables',
                            'interface', 'responses'):
                out.write('%s\n' % section)
                for line in getattr(self, section):
                    out.write("\t%s\n" % line)
                if section == 'interface' and data is not None:
                    for line in getattr(self, section):
                        if 'analysis_components' in line:
                            raise RuntimeError('Cannot specify both'
                                               ' analysis_components and data')
                    ident = str(id(data))
                    _USER_DATA[ident] = data
                    out.write("\t  analysis_components = '%s'\n" % ident)


def fetch_data(ident):
    """ Return user data object recorded by :meth:`DakotaInput.write`. """
    return _USER_DATA[ident]


class _ExcInfo(object):
    """ Used to hold exception return information. """

    def __init__(self):
        self.type = None
        self.value = None
        self.traceback = None


def run_dakota(infile, mpi_comm=None, use_mpi=True, stdout=None, stderr=None):
#def run_dakota(infile, mpi_comm=None, use_mpi=False, stdout=None, stderr=None):
    """
    Run DAKOTA with `infile`.

    If `mpi_comm` is not None, that is used as an MPI communicator.
    Otherwise, the ``world`` communicator from :class:`boost.mpi` is used
    if MPI is supported and `use_mpi` is True.

    `stdout` and `stderr` can be used to direct their respective DAKOTA
    stream to a filename.
    """

    # Checking for a Python exception via sys.exc_info() doesn't work, for
    # some reason it always returns (None, None, None).  So instead we pass
    # an object down and if an exception is thrown, the C++ level will fill
    # it with the exception information so we can re-raise it.
    err = 0
    exc = _ExcInfo()

    if mpi_comm is None:
        if _HAVE_MPI and use_mpi:
            try: 
                from boost.mpi import world
                err = pyDAKOTA.run_dakota_mpi(infile, world, stdout, stderr, exc)
            except ImportError: err = pyDAKOTA.run_dakota(infile, stdout, stderr, exc)
        else:
            err = pyDAKOTA.run_dakota(infile, stdout, stderr, exc)
    else:
        err = pyDAKOTA.run_dakota_mpi(infile, mpi_comm, stdout, stderr, exc)

    # Check for errors. We'll get here if Dakota::abort_mode has been set to
    # throw an exception rather than shut down the process.
    if err:
        if exc.type is None:
            raise RuntimeError('DAKOTA analysis failed')
        else:
            raise exc.type, exc.value, exc.traceback


def dakota_callback(**kwargs):
    """
    Generic callback from DAKOTA, forwards parameters to driver provided as
    the ``data`` argument to :meth:`DakotaInput.write`.

    The driver should return a responses dictionary based on the parameters.

    `kwargs` contains:

    =================== ==============================================
    Key                 Definition
    =================== ==============================================
    functions           number of functions (responses, constraints)
    ------------------- ----------------------------------------------
    variables           total number of variables
    ------------------- ----------------------------------------------
    cv                  list/array of continuous variable values
    ------------------- ----------------------------------------------
    div                 list/array of discrete integer variable values
    ------------------- ----------------------------------------------
    drv                 list/array of discrete real variable values
    ------------------- ----------------------------------------------
    av                  single list/array of all variable values
    ------------------- ----------------------------------------------
    cv_labels           continuous variable labels
    ------------------- ----------------------------------------------
    div_labels          discrete integer variable labels
    ------------------- ----------------------------------------------
    drv_labels          discrete real variable labels
    ------------------- ----------------------------------------------
    av_labels           all variable labels
    ------------------- ----------------------------------------------
    asv                 active set vector (bit1=f, bit2=df, bit3=d^2f)
    ------------------- ----------------------------------------------
    dvv                 derivative variables vector
    ------------------- ----------------------------------------------
    currEvalId          current evaluation ID number
    ------------------- ----------------------------------------------
    analysis_components list of strings from input file, the first is
                        assumed to be an identifier for a driver
                        object with a dakota_callback method
    =================== ==============================================

    """
    acs = kwargs['analysis_components']
    if not acs:
        msg = 'dakota_callback (%s): No analysis_components' % os.getpid()
        logging.error(msg)
        raise RuntimeError(msg)

    ident = acs[0]
    try:
        driver = fetch_data(ident)
    except KeyError:
        msg = 'dakota_callback (%s): ident %s not found in user data' \
                  % (os.getpid(), ident)
        logging.error(msg)
        raise RuntimeError(msg)

    return driver.dakota_callback(**kwargs)

