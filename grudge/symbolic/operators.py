"""Building blocks and mappers for operator expression trees."""

from __future__ import division, absolute_import

__copyright__ = "Copyright (C) 2008 Andreas Kloeckner"

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

from six.moves import intern

import numpy as np
import numpy.linalg as la
import pymbolic.primitives
from pytools import Record, memoize_method


# {{{ base classes

class Operator(pymbolic.primitives.Leaf):
    """
    .. attribute:: where

        *None* for the default volume discretization or a boundary
        tag for an operation on the denoted part of the boundary.
    """

    def __init__(self, where=None):
        self.where = where

    def stringifier(self):
        from grudge.symbolic.mappers import StringifyMapper
        return StringifyMapper

    def __call__(self, expr):
        from pytools.obj_array import with_object_array_or_scalar
        from grudge.tools import is_zero

        def bind_one(subexpr):
            if is_zero(subexpr):
                return subexpr
            else:
                from grudge.symbolic.primitives import OperatorBinding
                return OperatorBinding(self, subexpr)

        return with_object_array_or_scalar(bind_one, expr)

    def get_hash(self):
        return hash((self.__class__,) + (self.__getinitargs__()))

    def is_equal(self, other):
        return self.__class__ == other.__class__ and \
                self.__getinitargs__() == other.__getinitargs__()

    def __getinitargs__(self):
        return (self.where,)

# }}}


# {{{ sum, integral, max

class NodalReductionOperator(Operator):
    pass


class NodalSum(NodalReductionOperator):
    mapper_method = intern("map_nodal_sum")


class NodalMax(NodalReductionOperator):
    mapper_method = intern("map_nodal_max")


class NodalMin(NodalReductionOperator):
    mapper_method = intern("map_nodal_min")

# }}}


# {{{ differentiation operators

# {{{ global differentiation

class DiffOperatorBase(Operator):
    def __init__(self, xyz_axis):
        Operator.__init__(self)

        self.xyz_axis = xyz_axis

    def __getinitargs__(self):
        return (self.xyz_axis,)

    def preimage_ranges(self, eg):
        return eg.ranges

    def equal_except_for_axis(self, other):
        return (type(self) == type(other)
                # first argument is always the axis
                and self.__getinitargs__()[1:] == other.__getinitargs__()[1:])


class StrongFormDiffOperatorBase(DiffOperatorBase):
    pass


class WeakFormDiffOperatorBase(DiffOperatorBase):
    pass


class StiffnessOperator(StrongFormDiffOperatorBase):
    mapper_method = intern("map_stiffness")


class DifferentiationOperator(StrongFormDiffOperatorBase):
    mapper_method = intern("map_diff")


class StiffnessTOperator(WeakFormDiffOperatorBase):
    mapper_method = intern("map_stiffness_t")


class MInvSTOperator(WeakFormDiffOperatorBase):
    mapper_method = intern("map_minv_st")


class QuadratureStiffnessTOperator(DiffOperatorBase):
    """
    .. note::

        This operator is purely for internal use. It is inserted
        by :class:`grudge.symbolic.mappers.OperatorSpecializer`
        when a :class:`StiffnessTOperator` is applied to a quadrature
        field, and then eliminated by
        :class:`grudge.symbolic.mappers.GlobalToReferenceMapper`
        in favor of operators on the reference element.
    """

    def __init__(self, xyz_axis, input_quadrature_tag, where=None):
        super(QuadratureStiffnessTOperator, self).__init__(xyz_axis, where=where)
        self.input_quadrature_tag = input_quadrature_tag

    def __getinitargs__(self):
        return (self.xyz_axis, self.input_quadrature_tag, self.where)

    mapper_method = intern("map_quad_stiffness_t")


def DiffOperatorVector(els):
    from grudge.tools import join_fields
    return join_fields(*els)

# }}}


# {{{ reference-element differentiation

class ReferenceDiffOperatorBase(Operator):
    def __init__(self, rst_axis, where=None):
        super(ReferenceDiffOperatorBase, self).__init__(where)

        self.rst_axis = rst_axis

    def __getinitargs__(self):
        return (self.rst_axis, self.where)

    def equal_except_for_axis(self, other):
        return (type(self) == type(other)
                # first argument is always the axis
                and self.__getinitargs__()[1:] == other.__getinitargs__()[1:])


class ReferenceDifferentiationOperator(ReferenceDiffOperatorBase):
    mapper_method = intern("map_ref_diff")


class ReferenceStiffnessTOperator(ReferenceDiffOperatorBase):
    mapper_method = intern("map_ref_stiffness_t")


class ReferenceQuadratureStiffnessTOperator(ReferenceDiffOperatorBase):
    """
    .. note::

        This operator is purely for internal use. It is inserted
        by :class:`grudge.symbolic.mappers.OperatorSpecializer`
        when a :class:`StiffnessTOperator` is applied to a quadrature field.
    """

    def __init__(self, rst_axis, input_quadrature_tag, where=None):
        ReferenceDiffOperatorBase.__init__(self, rst_axis, where)
        self.input_quadrature_tag = input_quadrature_tag

    def __getinitargs__(self):
        return (self.rst_axis,
                self.input_quadrature_tag,
                self.where)

    mapper_method = intern("map_ref_quad_stiffness_t")

# }}}

# }}}


# {{{ elementwise operators

class ElementwiseLinearOperator(Operator):
    def matrix(self, element_group):
        raise NotImplementedError

    mapper_method = intern("map_elementwise_linear")


class ElementwiseMaxOperator(Operator):
    mapper_method = intern("map_elementwise_max")


# {{{ quadrature upsamplers

class QuadratureGridUpsampler(Operator):
    """In a user-specified optemplate, this operator can be used to interpolate
    volume and boundary data to their corresponding quadrature grids.

    In pre-processing, the boundary quad interpolation is specialized to
    a separate operator, :class:`QuadratureBoundaryGridUpsampler`.
    """
    def __init__(self, quadrature_tag, where=None):
        self.quadrature_tag = quadrature_tag
        self.where = where

    def __getinitargs__(self):
        return (self.quadrature_tag,)

    mapper_method = intern("map_quad_grid_upsampler")


class QuadratureInteriorFacesGridUpsampler(Operator):
    """Interpolates nodal volume data to interior face data on a quadrature
    grid.

    Note that the "interior faces" grid includes faces lying opposite to the
    boundary.
    """
    def __init__(self, quadrature_tag):
        self.quadrature_tag = quadrature_tag

    def __getinitargs__(self):
        return (self.quadrature_tag,)

    mapper_method = intern("map_quad_int_faces_grid_upsampler")

# }}}


# {{{ various elementwise linear operators

class FilterOperator(ElementwiseLinearOperator):
    def __init__(self, mode_response_func):
        """
        :param mode_response_func: A function mapping
          ``(mode_tuple, local_discretization)`` to a float indicating the
          factor by which this mode is to be multiplied after filtering.
          (For example an instance of
          :class:`ExponentialFilterResponseFunction`.
        """
        self.mode_response_func = mode_response_func

    def __getinitargs__(self):
        return (self.mode_response_func,)

    def matrix(self, eg):
        ldis = eg.local_discretization

        filter_coeffs = [self.mode_response_func(mid, ldis)
            for mid in ldis.generate_mode_identifiers()]

        # build filter matrix
        vdm = ldis.vandermonde()
        from grudge.tools import leftsolve
        mat = np.asarray(
            leftsolve(vdm,
                np.dot(vdm, np.diag(filter_coeffs))),
            order="C")

        return mat


class OnesOperator(ElementwiseLinearOperator):
    def matrix(self, eg):
        ldis = eg.local_discretization

        node_count = ldis.node_count()
        return np.ones((node_count, node_count), dtype=np.float64)


class AveragingOperator(ElementwiseLinearOperator):
    def matrix(self, eg):
        # average matrix, so that AVE*fields = cellaverage(fields)
        # see Hesthaven and Warburton page 227

        mmat = eg.local_discretization.mass_matrix()
        standard_el_vol = np.sum(np.dot(mmat, np.ones(mmat.shape[0])))
        avg_mat_row = np.sum(mmat, 0)/standard_el_vol

        avg_mat = np.zeros((np.size(avg_mat_row), np.size(avg_mat_row)))
        avg_mat[:] = avg_mat_row
        return avg_mat


class InverseVandermondeOperator(ElementwiseLinearOperator):
    def matrix(self, eg):
        return np.asarray(
                la.inv(eg.local_discretization.vandermonde()),
                order="C")


class VandermondeOperator(ElementwiseLinearOperator):
    def matrix(self, eg):
        return np.asarray(
                eg.local_discretization.vandermonde(),
                order="C")

# }}}

# }}}


# {{{ mass operators

class MassOperatorBase(Operator):
    pass


class MassOperator(MassOperatorBase):
    mapper_method = intern("map_mass")


class InverseMassOperator(MassOperatorBase):
    mapper_method = intern("map_inverse_mass")


class QuadratureMassOperator(Operator):
    """
    .. note::

        This operator is purely for internal use. It is inserted
        by :class:`grudge.symbolic.mappers.OperatorSpecializer`
        when a :class:`StiffnessTOperator` is applied to a quadrature
        field, and then eliminated by
        :class:`grudge.symbolic.mappers.GlobalToReferenceMapper`
        in favor of operators on the reference element.
    """

    def __init__(self, quadrature_tag):
        self.quadrature_tag = quadrature_tag

    def __getinitargs__(self):
        return (self.quadrature_tag,)

    mapper_method = intern("map_quad_mass")


class ReferenceQuadratureMassOperator(Operator):
    """
    .. note::

        This operator is purely for internal use. It is inserted
        by :class:`grudge.symbolic.mappers.OperatorSpecializer`
        when a :class:`MassOperator` is applied to a quadrature field.
    """

    def __init__(self, quadrature_tag, where=None):
        super(Operator, self).__init__(self.where)
        self.quadrature_tag = quadrature_tag

    def __getinitargs__(self):
        return (self.quadrature_tag, self.where)

    mapper_method = intern("map_ref_quad_mass")


class ReferenceMassOperatorBase(MassOperatorBase):
    pass


class ReferenceMassOperator(ReferenceMassOperatorBase):
    @staticmethod
    def matrix(element_group):
        import modepy as mp
        return mp.mass_matrix(
                element_group.basis(),
                element_group.unit_nodes)

    mapper_method = intern("map_ref_mass")


class ReferenceInverseMassOperator(ReferenceMassOperatorBase):
    @staticmethod
    def matrix(element_group):
        import modepy as mp
        return mp.inverse_mass_matrix(
                element_group.basis(),
                element_group.unit_nodes)

    mapper_method = intern("map_ref_inverse_mass")

# }}}


# {{{ boundary-related operators

class BoundarizeOperator(Operator):
    def __init__(self, tag):
        self.tag = tag

    def __getinitargs__(self):
        return (self.tag,)

    mapper_method = intern("map_boundarize")


class FluxExchangeOperator(pymbolic.primitives.AlgebraicLeaf):
    """An operator that results in the sending and receiving of
    boundary information for its argument fields.
    """

    def __init__(self, idx, rank, arg_fields):
        self.index = idx
        self.rank = rank
        self.arg_fields = arg_fields

        # only tuples are hashable
        if not isinstance(arg_fields, tuple):
            raise TypeError("FluxExchangeOperator: arg_fields must be a tuple")

    def __getinitargs__(self):
        return (self.index, self.rank, self.arg_fields)

    def get_hash(self):
        return hash((self.__class__, self.index, self.rank, self.arg_fields))

    mapper_method = intern("map_flux_exchange")

    def is_equal(self, other):
        return self.__class__ == other.__class__ and \
                self.__getinitargs__() == other.__getinitargs__()

# }}}


# {{{ flux-like operators

class FluxOperatorBase(Operator):
    def __init__(self, flux, is_lift=False):
        Operator.__init__(self)
        self.flux = flux
        self.is_lift = is_lift

    def get_flux_or_lift_text(self):
        if self.is_lift:
            return "Lift"
        else:
            return "Flux"

    def repr_op(self):
        """Return an equivalent operator with the flux expression set to 0."""
        return type(self)(0, *self.__getinitargs__()[1:])

    def __call__(self, arg):
        # override to suppress apply-operator-to-each-operand
        # behavior from superclass

        from grudge.symbolic.primitives import OperatorBinding
        return OperatorBinding(self, arg)

    def __mul__(self, arg):
        from warnings import warn
        warn("Multiplying by a flux operator is deprecated. "
                "Use the less ambiguous parenthesized syntax instead.",
                DeprecationWarning, stacklevel=2)
        return self.__call__(arg)


class QuadratureFluxOperatorBase(FluxOperatorBase):
    pass


class BoundaryFluxOperatorBase(FluxOperatorBase):
    pass


class FluxOperator(FluxOperatorBase):
    def __getinitargs__(self):
        return (self.flux, self.is_lift)

    mapper_method = intern("map_flux")


class BoundaryFluxOperator(BoundaryFluxOperatorBase):
    """
    .. note::

        This operator is purely for internal use. It is inserted
        by :class:`grudge.symbolic.mappers.OperatorSpecializer`
        when a :class:`FluxOperator` is applied to a boundary field.
    """
    def __init__(self, flux, boundary_tag, is_lift=False):
        FluxOperatorBase.__init__(self, flux, is_lift)
        self.boundary_tag = boundary_tag

    def __getinitargs__(self):
        return (self.flux, self.boundary_tag, self.is_lift)

    mapper_method = intern("map_bdry_flux")


class QuadratureFluxOperator(QuadratureFluxOperatorBase):
    """
    .. note::

        This operator is purely for internal use. It is inserted
        by :class:`grudge.symbolic.mappers.OperatorSpecializer`
        when a :class:`FluxOperator` is applied to a quadrature field.
    """

    def __init__(self, flux, quadrature_tag):
        FluxOperatorBase.__init__(self, flux, is_lift=False)

        self.quadrature_tag = quadrature_tag

    def __getinitargs__(self):
        return (self.flux, self.quadrature_tag)

    mapper_method = intern("map_quad_flux")


class QuadratureBoundaryFluxOperator(
        QuadratureFluxOperatorBase, BoundaryFluxOperatorBase):
    """
    .. note::

        This operator is purely for internal use. It is inserted
        by :class:`grudge.symbolic.mappers.OperatorSpecializer`
        when a :class:`FluxOperator` is applied to a quadrature
        boundary field.
    """
    def __init__(self, flux, quadrature_tag, boundary_tag):
        FluxOperatorBase.__init__(self, flux, is_lift=False)
        self.quadrature_tag = quadrature_tag
        self.boundary_tag = boundary_tag

    def __getinitargs__(self):
        return (self.flux, self.quadrature_tag, self.boundary_tag)

    mapper_method = intern("map_quad_bdry_flux")


class VectorFluxOperator(object):
    """Note that this isn't an actual operator. It's just a placeholder that pops
    out a vector of FluxOperators when applied to an operand.
    """
    def __init__(self, fluxes):
        self.fluxes = fluxes

    def __call__(self, arg):
        if isinstance(arg, int) and arg == 0:
            return 0
        from pytools.obj_array import make_obj_array
        from grudge.symbolic.primitives import OperatorBinding

        return make_obj_array(
                [OperatorBinding(FluxOperator(f), arg)
                    for f in self.fluxes])

    def __mul__(self, arg):
        from warnings import warn
        warn("Multiplying by a vector flux operator is deprecated. "
                "Use the less ambiguous parenthesized syntax instead.",
                DeprecationWarning, stacklevel=2)
        return self.__call__(arg)


class WholeDomainFluxOperator(pymbolic.primitives.AlgebraicLeaf):
    """Used by the CUDA backend to represent a flux computation on the
    whole domain--interior and boundary.

    Unlike other operators, :class:`WholeDomainFluxOperator` instances
    are not bound.
    """

    class FluxInfo(Record):
        __slots__ = []

        def __repr__(self):
            # override because we want flux_expr in infix
            return "%s(%s)" % (
                    self.__class__.__name__,
                    ", ".join("%s=%s" % (fld, getattr(self, fld))
                        for fld in self.__class__.fields
                        if hasattr(self, fld)))

    class InteriorInfo(FluxInfo):
        # attributes: flux_expr, field_expr,

        @property
        @memoize_method
        def dependencies(self):
            from grudge.symbolic.tools import get_flux_dependencies
            return set(get_flux_dependencies(
                self.flux_expr, self.field_expr))

    class BoundaryInfo(FluxInfo):
        # attributes: flux_expr, bpair

        @property
        @memoize_method
        def int_dependencies(self):
            from grudge.symbolic.tools import get_flux_dependencies
            return set(get_flux_dependencies(
                    self.flux_expr, self.bpair, bdry="int"))

        @property
        @memoize_method
        def ext_dependencies(self):
            from grudge.symbolic.tools import get_flux_dependencies
            return set(get_flux_dependencies(
                    self.flux_expr, self.bpair, bdry="ext"))

    def __init__(self, is_lift, interiors, boundaries,
            quadrature_tag):
        from grudge.symbolic.tools import get_flux_dependencies

        self.is_lift = is_lift

        self.interiors = tuple(interiors)
        self.boundaries = tuple(boundaries)
        self.quadrature_tag = quadrature_tag

        from pytools import set_sum
        interior_deps = set_sum(iflux.dependencies
                for iflux in interiors)
        boundary_int_deps = set_sum(bflux.int_dependencies
                for bflux in boundaries)
        boundary_ext_deps = set_sum(bflux.ext_dependencies
                for bflux in boundaries)

        self.interior_deps = list(interior_deps)
        self.boundary_int_deps = list(boundary_int_deps)
        self.boundary_ext_deps = list(boundary_ext_deps)
        self.boundary_deps = list(boundary_int_deps | boundary_ext_deps)

        self.dep_to_tag = {}
        for bflux in boundaries:
            for dep in get_flux_dependencies(
                    bflux.flux_expr, bflux.bpair, bdry="ext"):
                self.dep_to_tag[dep] = bflux.bpair.tag

    def stringifier(self):
        from grudge.symbolic.mappers import StringifyMapper
        return StringifyMapper

    def repr_op(self):
        return type(self)(False, [], [], self.quadrature_tag)

    @memoize_method
    def rebuild_optemplate(self):
        def generate_summands():
            for i in self.interiors:
                if self.quadrature_tag is None:
                    yield FluxOperator(
                            i.flux_expr, self.is_lift)(i.field_expr)
                else:
                    yield QuadratureFluxOperator(
                            i.flux_expr, self.quadrature_tag)(i.field_expr)
            for b in self.boundaries:
                if self.quadrature_tag is None:
                    yield BoundaryFluxOperator(
                            b.flux_expr, b.bpair.tag, self.is_lift)(b.bpair)
                else:
                    yield QuadratureBoundaryFluxOperator(
                            b.flux_expr, self.quadrature_tag,
                            b.bpair.tag)(b.bpair)

        from pymbolic.primitives import flattened_sum
        return flattened_sum(generate_summands())

    # infrastructure interaction
    def get_hash(self):
        return hash((self.__class__, self.rebuild_optemplate()))

    def is_equal(self, other):
        return (other.__class__ == WholeDomainFluxOperator
                and self.rebuild_optemplate() == other.rebuild_optemplate())

    def __getinitargs__(self):
        return (self.is_lift, self.interiors, self.boundaries,
                self.quadrature_tag)

    mapper_method = intern("map_whole_domain_flux")

# }}}


# {{{ convenience functions for operator creation

def get_flux_operator(flux):
    """Return a flux operator that can be multiplied with
    a volume field to obtain the interior fluxes
    or with a :class:`BoundaryPair` to obtain the lifted boundary
    flux.
    """
    from pytools.obj_array import is_obj_array
    from grudge.symbolic.operators import VectorFluxOperator, FluxOperator

    if is_obj_array(flux):
        return VectorFluxOperator(flux)
    else:
        return FluxOperator(flux)


def nabla(dim):
    from pytools.obj_array import make_obj_array
    return make_obj_array(
            [DifferentiationOperator(i) for i in range(dim)])


def minv_stiffness_t(dim):
    from pytools.obj_array import make_obj_array
    return make_obj_array(
        [MInvSTOperator(i) for i in range(dim)])


def stiffness(dim):
    from pytools.obj_array import make_obj_array
    return make_obj_array(
        [StiffnessOperator(i) for i in range(dim)])


def stiffness_t(dim):
    from pytools.obj_array import make_obj_array
    return make_obj_array(
        [StiffnessTOperator(i) for i in range(dim)])


def integral(arg):
    from grudge import sym
    return sym.NodalSum()(sym.MassOperator()(sym.Ones())*arg)


def norm(p, arg):
    """
    :arg arg: is assumed to be a vector, i.e. have shape ``(n,)``.
    """
    import grudge.symbolic as sym

    if p == 2:
        comp_norm_squared = sym.NodalSum()(
                sym.CFunction("fabs")(
                    arg * sym.MassOperator()(arg)))
        return sym.CFunction("sqrt")(sum(comp_norm_squared))

    elif p == np.Inf:
        comp_norm = sym.NodalMax()(sym.CFunction("fabs")(arg))
        from pymbolic.primitives import Max
        return reduce(Max, comp_norm)

    else:
        return sum(sym.NodalSum()(sym.CFunction("fabs")(arg)**p))**(1/p)

# }}}

# vim: foldmethod=marker
