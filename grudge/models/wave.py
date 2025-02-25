"""Wave equation operators."""

__copyright__ = """
Copyright (C) 2009 Andreas Kloeckner
Copyright (C) 2021 University of Illinois Board of Trustees
"""

__license__ = """
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""


import numpy as np

from arraycontext import thaw, freeze

from grudge.models import HyperbolicOperator

from meshmode.mesh import BTAG_ALL, BTAG_NONE

from pytools.obj_array import flat_obj_array

import grudge.op as op


# {{{ constant-velocity

class WeakWaveOperator(HyperbolicOperator):
    r"""This operator discretizes the wave equation
    :math:`\partial_t^2 u = c^2 \Delta u`.

    To be precise, we discretize the hyperbolic system

    .. math::

        \partial_t u - c \\nabla \\cdot v = 0

        \partial_t v - c \\nabla u = 0

    The sign of :math:`v` determines whether we discretize the forward or the
    backward wave equation.

    :math:`c` is assumed to be constant across all space.
    """

    def __init__(self, dcoll, c, source_f=None,
            flux_type="upwind",
            dirichlet_tag=BTAG_ALL,
            dirichlet_bc_f=0,
            neumann_tag=BTAG_NONE,
            radiation_tag=BTAG_NONE):

        if source_f is None:
            source_f = lambda actx, dcoll, t: dcoll.zeros(actx)  # noqa: E731

        self.dcoll = dcoll
        self.c = c
        self.source_f = source_f

        if self.c > 0:
            self.sign = 1
        else:
            self.sign = -1

        self.dirichlet_tag = dirichlet_tag
        self.neumann_tag = neumann_tag
        self.radiation_tag = radiation_tag

        self.dirichlet_bc_f = dirichlet_bc_f

        self.flux_type = flux_type

    def flux(self, wtpair):
        u = wtpair[0]
        v = wtpair[1:]
        actx = u.int.array_context
        normal = thaw(self.dcoll.normal(wtpair.dd), actx)

        central_flux_weak = -self.c*flat_obj_array(
                np.dot(v.avg, normal),
                u.avg * normal)

        if self.flux_type == "central":
            return central_flux_weak
        elif self.flux_type == "upwind":
            return central_flux_weak - self.c*self.sign*flat_obj_array(
                    0.5*(u.ext-u.int),
                    0.5*(normal * np.dot(normal, v.ext-v.int)))
        else:
            raise ValueError("invalid flux type '%s'" % self.flux_type)

    def operator(self, t, w):
        dcoll = self.dcoll
        u = w[0]
        v = w[1:]
        actx = u.array_context

        # boundary conditions -------------------------------------------------

        # dirichlet BCs -------------------------------------------------------
        dir_u = op.project(dcoll, "vol", self.dirichlet_tag, u)
        dir_v = op.project(dcoll, "vol", self.dirichlet_tag, v)
        if self.dirichlet_bc_f:
            # FIXME
            from warnings import warn
            warn("Inhomogeneous Dirichlet conditions on the wave equation "
                    "are still having issues.")

            dir_g = self.dirichlet_bc_f
            dir_bc = flat_obj_array(2*dir_g - dir_u, dir_v)
        else:
            dir_bc = flat_obj_array(-dir_u, dir_v)

        # neumann BCs ---------------------------------------------------------
        neu_u = op.project(dcoll, "vol", self.neumann_tag, u)
        neu_v = op.project(dcoll, "vol", self.neumann_tag, v)
        neu_bc = flat_obj_array(neu_u, -neu_v)

        # radiation BCs -------------------------------------------------------
        rad_normal = thaw(dcoll.normal(dd=self.radiation_tag), actx)

        rad_u = op.project(dcoll, "vol", self.radiation_tag, u)
        rad_v = op.project(dcoll, "vol", self.radiation_tag, v)

        rad_bc = flat_obj_array(
            0.5*(rad_u - self.sign*np.dot(rad_normal, rad_v)),
            0.5*rad_normal*(np.dot(rad_normal, rad_v) - self.sign*rad_u)
        )

        # entire operator -----------------------------------------------------
        def flux(tpair):
            return op.project(dcoll, tpair.dd, "all_faces", self.flux(tpair))

        result = (
            op.inverse_mass(
                dcoll,
                flat_obj_array(
                    -self.c*op.weak_local_div(dcoll, v),
                    -self.c*op.weak_local_grad(dcoll, u)
                )
                - op.face_mass(
                    dcoll,
                    sum(flux(tpair) for tpair in op.interior_trace_pairs(dcoll, w))
                    + flux(op.bv_trace_pair(dcoll, self.dirichlet_tag, w, dir_bc))
                    + flux(op.bv_trace_pair(dcoll, self.neumann_tag, w, neu_bc))
                    + flux(op.bv_trace_pair(dcoll, self.radiation_tag, w, rad_bc))
                )
            )
        )

        result[0] = result[0] + self.source_f(actx, dcoll, t)

        return result

    def check_bc_coverage(self, mesh):
        from meshmode.mesh import check_bc_coverage
        check_bc_coverage(mesh, [
            self.dirichlet_tag,
            self.neumann_tag,
            self.radiation_tag])

    def max_characteristic_velocity(self, actx, t=None, fields=None):
        return abs(self.c)


# }}}


# {{{ variable-velocity

class VariableCoefficientWeakWaveOperator(HyperbolicOperator):
    r"""This operator discretizes the wave equation
    :math:`\partial_t^2 u = c^2 \Delta u`.

    To be precise, we discretize the hyperbolic system

    .. math::

        \partial_t u - c \\nabla \\cdot v = 0

        \partial_t v - c \\nabla u = 0

    The sign of :math:`v` determines whether we discretize the forward or the
    backward wave equation.
    """

    def __init__(self, dcoll, c, source_f=None,
            flux_type="upwind",
            dirichlet_tag=BTAG_ALL,
            dirichlet_bc_f=0,
            neumann_tag=BTAG_NONE,
            radiation_tag=BTAG_NONE):
        """
        :arg c: a thawed (with *actx*) :class:`~meshmode.dof_array.DOFArray`
            representing the propogation speed of the wave.
        """

        if source_f is None:
            source_f = lambda actx, dcoll, t: dcoll.zeros(actx)  # noqa: E731

        actx = c.array_context
        self.dcoll = dcoll
        self.c = freeze(c)
        self.source_f = source_f

        ones = dcoll.zeros(actx) + 1
        self.sign = freeze(actx.np.where(c > 0, ones, -ones))

        self.dirichlet_tag = dirichlet_tag
        self.neumann_tag = neumann_tag
        self.radiation_tag = radiation_tag

        self.dirichlet_bc_f = dirichlet_bc_f

        self.flux_type = flux_type

    def flux(self, wtpair):
        c = wtpair[0]
        u = wtpair[1]
        v = wtpair[2:]
        actx = u.int.array_context
        normal = thaw(self.dcoll.normal(wtpair.dd), actx)

        flux_central_weak = -0.5 * flat_obj_array(
            np.dot(v.int*c.int + v.ext*c.ext, normal),
            (u.int * c.int + u.ext*c.ext) * normal)

        if self.flux_type == "central":
            return flux_central_weak

        elif self.flux_type == "upwind":
            return flux_central_weak - 0.5 * flat_obj_array(
                    c.ext*u.ext - c.int * u.int,

                    normal * (np.dot(normal, c.ext * v.ext - c.int * v.int)))

        else:
            raise ValueError("invalid flux type '%s'" % self.flux_type)

    def operator(self, t, w):
        dcoll = self.dcoll
        u = w[0]
        v = w[1:]
        actx = u.array_context

        c = thaw(self.c, actx)

        flux_w = flat_obj_array(c, w)

        # boundary conditions -------------------------------------------------

        # dirichlet BCs -------------------------------------------------------
        dir_c = op.project(dcoll, "vol", self.dirichlet_tag, c)
        dir_u = op.project(dcoll, "vol", self.dirichlet_tag, u)
        dir_v = op.project(dcoll, "vol", self.dirichlet_tag, v)
        if self.dirichlet_bc_f:
            # FIXME
            from warnings import warn
            warn("Inhomogeneous Dirichlet conditions on the wave equation "
                    "are still having issues.")

            dir_g = self.dirichlet_bc_f
            dir_bc = flat_obj_array(dir_c, 2*dir_g - dir_u, dir_v)
        else:
            dir_bc = flat_obj_array(dir_c, -dir_u, dir_v)

        # neumann BCs ---------------------------------------------------------
        neu_c = op.project(dcoll, "vol", self.neumann_tag, c)
        neu_u = op.project(dcoll, "vol", self.neumann_tag, u)
        neu_v = op.project(dcoll, "vol", self.neumann_tag, v)
        neu_bc = flat_obj_array(neu_c, neu_u, -neu_v)

        # radiation BCs -------------------------------------------------------
        rad_normal = thaw(dcoll.normal(dd=self.radiation_tag), actx)

        rad_c = op.project(dcoll, "vol", self.radiation_tag, c)
        rad_u = op.project(dcoll, "vol", self.radiation_tag, u)
        rad_v = op.project(dcoll, "vol", self.radiation_tag, v)
        rad_sign = op.project(dcoll, "vol", self.radiation_tag,
                thaw(self.sign, actx))

        rad_bc = flat_obj_array(
            rad_c,
            0.5*(rad_u - rad_sign * np.dot(rad_normal, rad_v)),
            0.5*rad_normal*(np.dot(rad_normal, rad_v) - rad_sign*rad_u)
        )

        # entire operator -----------------------------------------------------
        def flux(tpair):
            return op.project(dcoll, tpair.dd, "all_faces", self.flux(tpair))

        result = (
            op.inverse_mass(
                dcoll,
                flat_obj_array(
                    -c*op.weak_local_div(dcoll, v),
                    -c*op.weak_local_grad(dcoll, u)
                )
                - op.face_mass(
                    dcoll,
                    sum(flux(tpair)
                        for tpair in op.interior_trace_pairs(dcoll, flux_w))
                    + flux(op.bv_trace_pair(dcoll, self.dirichlet_tag,
                                            flux_w, dir_bc))
                    + flux(op.bv_trace_pair(dcoll, self.neumann_tag,
                                            flux_w, neu_bc))
                    + flux(op.bv_trace_pair(dcoll, self.radiation_tag,
                                            flux_w, rad_bc))
                )
            )
        )

        result[0] = result[0] + self.source_f(actx, dcoll, t)

        return result

    def check_bc_coverage(self, mesh):
        from meshmode.mesh import check_bc_coverage
        check_bc_coverage(mesh, [
            self.dirichlet_tag,
            self.neumann_tag,
            self.radiation_tag])

    def max_characteristic_velocity(self, actx, **kwargs):
        return actx.np.fabs(thaw(self.c, actx))

# }}}


# vim: foldmethod=marker
