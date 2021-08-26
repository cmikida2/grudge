__copyright__ = """
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


from arraycontext import (
    ArrayContext,
    make_loopy_program,
    thaw
)
from meshmode.transform_metadata import FirstAxisIsElementsTag

from grudge.discretization import DiscretizationCollection
from grudge.op import (
    reference_mass_matrix,
    reference_inverse_mass_matrix,
    reference_derivative_matrices
)

from meshmode.dof_array import DOFArray

from pytools import memoize_in, keyed_memoize_in
from pytools.obj_array import obj_array_vectorize

import grudge.dof_desc as dof_desc
import numpy as np


def volume_quadrature_interpolation_matrix(
    actx: ArrayContext, base_element_group, quad_element_group):
    """todo.
    """
    @keyed_memoize_in(
        actx, volume_quadrature_interpolation_matrix,
        lambda base_grp, quad_grp: (base_grp.discretization_key(),
                                    quad_grp.discretization_key()))
    def get_volume_vand(base_grp, quad_grp):
        from modepy import vandermonde

        basis = base_grp.basis_obj()
        vdm_inv = np.linalg.inv(vandermonde(basis.functions,
                                            base_grp.unit_nodes))
        vdm_q = vandermonde(basis.functions, quad_grp.unit_nodes) @ vdm_inv
        return actx.freeze(actx.from_numpy(vdm_q))

    return get_volume_vand(base_element_group, quad_element_group)


def surface_quadrature_interpolation_matrix(
    actx: ArrayContext, base_element_group, quad_face_element_group, dtype):
    """todo.
    """
    @keyed_memoize_in(
        actx, surface_quadrature_interpolation_matrix,
        lambda base_grp, quad_face_grp: (base_grp.discretization_key(),
                                         quad_face_grp.discretization_key()))
    def get_surface_vand(base_grp, quad_face_grp):
        nfaces = base_grp.mesh_el_group.nfaces
        assert quad_face_grp.nelements == nfaces * base_grp.nelements

        matrix = np.empty(
            (quad_face_grp.nunit_dofs,
            nfaces,
            base_grp.nunit_dofs),
            dtype=dtype
        )

        from modepy import vandermonde, faces_for_shape

        basis = base_grp.basis_obj()
        vdm_inv = np.linalg.inv(vandermonde(basis.functions,
                                            base_grp.unit_nodes))
        faces = faces_for_shape(base_grp.shape)
        # NOTE: Assumes same quadrature rule on each face
        face_quadrature = quad_face_grp.quadrature_rule()

        surface_nodes = np.concatenate(
            [face.map_to_volume(face_quadrature.nodes) for face in faces],
            axis=1
        )
        return actx.freeze(
            actx.from_numpy(
                vandermonde(basis.functions, surface_nodes) @ vdm_inv
            )
        )

    return get_surface_vand(base_element_group, quad_face_element_group)


def quadrature_volume_interpolation(dcoll: DiscretizationCollection, dq, vec):
    if isinstance(vec, np.ndarray):
        return obj_array_vectorize(
            lambda el: quadrature_volume_interpolation(dcoll, dq, el), vec)

    actx = vec.array_context
    discr = dcoll.discr_from_dd("vol")
    quad_discr = dcoll.discr_from_dd(dq)

    return DOFArray(
        actx,
        data=tuple(
            actx.einsum("ij,ej->ei",
                        volume_quadrature_interpolation_matrix(
                            actx,
                            base_element_group=bgrp,
                            quad_element_group=qgrp
                        ),
                        vec_i,
                        arg_names=("Vq_mat", "vec"),
                        tagged=(FirstAxisIsElementsTag(),))

            for bgrp, qgrp, vec_i in zip(discr.groups, quad_discr.groups, vec)
        )
    )


def map_to_nodal_from_volume(dcoll: DiscretizationCollection, dq, vec):
    if isinstance(vec, np.ndarray):
        return obj_array_vectorize(
            lambda el: map_to_nodal_from_volume(dcoll, dq, el), vec)

    actx = vec.array_context
    discr = dcoll.discr_from_dd("vol")
    quad_discr = dcoll.discr_from_dd(dq)

    return DOFArray(
        actx,
        data=tuple(
            actx.einsum("ji,ej->ei",
                        volume_quadrature_interpolation_matrix(
                            actx,
                            base_element_group=bgrp,
                            quad_element_group=qgrp
                        ),
                        vec_i,
                        arg_names=("Vq_mat", "vec"),
                        tagged=(FirstAxisIsElementsTag(),))

            for bgrp, qgrp, vec_i in zip(discr.groups, quad_discr.groups, vec)
        )
    )


def quadrature_surface_interpolation(dcoll: DiscretizationCollection, df, vec):
    if isinstance(vec, np.ndarray):
        return obj_array_vectorize(
            lambda el: quadrature_surface_interpolation(dcoll, df, el), vec)

    actx = vec.array_context
    dtype = vec.entry_dtype
    discr = dcoll.discr_from_dd("vol")
    quad_face_discr = dcoll.discr_from_dd(df)

    return DOFArray(
        actx,
        data=tuple(
            actx.einsum("ij,ej->ei",
                        surface_quadrature_interpolation_matrix(
                            actx,
                            base_element_group=bgrp,
                            quad_face_element_group=qfgrp,
                            dtype=dtype
                        ),
                        vec_i,
                        arg_names=("Vf_mat", "vec"),
                        tagged=(FirstAxisIsElementsTag(),))

            for bgrp, qfgrp, vec_i in zip(discr.groups,
                                          quad_face_discr.groups, vec)
        )
    )


def map_to_nodal_from_surface(dcoll: DiscretizationCollection, df, vec):
    if isinstance(vec, np.ndarray):
        return obj_array_vectorize(
            lambda el: map_to_nodal_from_surface(dcoll, df, el), vec)

    actx = vec.array_context
    dtype = vec.entry_dtype
    discr = dcoll.discr_from_dd("vol")
    quad_face_discr = dcoll.discr_from_dd(df)

    return DOFArray(
        actx,
        data=tuple(
            actx.einsum("ji,ej->ei",
                        surface_quadrature_interpolation_matrix(
                            actx,
                            base_element_group=bgrp,
                            quad_face_element_group=qfgrp,
                            dtype=dtype
                        ),
                        vec_i,
                        arg_names=("Vf_mat", "vec"),
                        tagged=(FirstAxisIsElementsTag(),))

            for bgrp, qfgrp, vec_i in zip(discr.groups,
                                          quad_face_discr.groups, vec)
        )
    )


def quadrature_based_mass_matrix(
    actx: ArrayContext, base_element_group, quad_element_group):
    """todo.
    """
    @keyed_memoize_in(
        actx, quadrature_based_mass_matrix,
        lambda base_grp, quad_grp: (base_grp.discretization_key(),
                                    quad_grp.discretization_key()))
    def get_ref_quad_mass_mat(base_grp, quad_grp):
        vdm_q = actx.to_numpy(
            thaw(
                volume_quadrature_interpolation_matrix(actx, base_grp, quad_grp),
                actx
            )
        )
        weights = np.diag(quad_grp.quadrature_rule().weights)

        return actx.freeze(actx.from_numpy(vdm_q.T @ weights @ vdm_q))

    return get_ref_quad_mass_mat(base_element_group, quad_element_group)


def quadrature_based_inverse_mass_matrix(
    actx: ArrayContext, base_element_group, quad_element_group):
    """todo.
    """
    @keyed_memoize_in(
        actx, quadrature_based_inverse_mass_matrix,
        lambda base_grp, quad_grp: (base_grp.discretization_key(),
                                    quad_grp.discretization_key()))
    def get_ref_quad_inv_mass_mat(base_grp, quad_grp):
        mass_mat = actx.to_numpy(
            thaw(quadrature_based_mass_matrix(actx, base_grp, quad_grp), actx)
        )
        return actx.freeze(actx.from_numpy(np.linalg.inv(mass_mat)))

    return get_ref_quad_inv_mass_mat(base_element_group, quad_element_group)


def quadrature_based_l2_projection_matrix(
    actx: ArrayContext, base_element_group, quad_element_group):
    """todo.
    """
    @keyed_memoize_in(
        actx, quadrature_based_l2_projection_matrix,
        lambda base_grp, quad_grp: (base_grp.discretization_key(),
                                    quad_grp.discretization_key()))
    def get_ref_l2_proj_mat(base_grp, quad_grp):
        vdm_q = actx.to_numpy(
            thaw(
                volume_quadrature_interpolation_matrix(actx, base_grp, quad_grp),
                actx
            )
        )
        weights = np.diag(quad_grp.quadrature_rule().weights)
        inv_mass_mat = actx.to_numpy(
            thaw(quadrature_based_inverse_mass_matrix(
                actx, base_grp, quad_grp), actx)
        )
        return actx.freeze(actx.from_numpy(inv_mass_mat @ (vdm_q.T @ weights)))

    return get_ref_l2_proj_mat(base_element_group, quad_element_group)


def quadrature_project(dcoll: DiscretizationCollection, dd_q, vec):
    if isinstance(vec, np.ndarray):
        return obj_array_vectorize(
                lambda el: quadrature_project(dcoll, dd_q, el), vec)

    actx = vec.array_context
    discr = dcoll.discr_from_dd("vol")
    quad_discr = dcoll.discr_from_dd(dd_q)

    return DOFArray(
        actx,
        data=tuple(
            actx.einsum("ij,ej->ei",
                        quadrature_based_l2_projection_matrix(
                            actx,
                            base_element_group=bgrp,
                            quad_element_group=qgrp
                        ),
                        vec_i,
                        arg_names=("P_mat", "vec"),
                        tagged=(FirstAxisIsElementsTag(),))

            for bgrp, qgrp, vec_i in zip(discr.groups, quad_discr.groups, vec)
        )
    )


def boundary_matrices(
    actx: ArrayContext, base_element_group, face_quad_element_group):
    """todo.
    """
    @keyed_memoize_in(
        actx, boundary_matrices,
        lambda face_grp, vol_grp: (face_grp.discretization_key(),
                                   vol_grp.discretization_key()))
    def get_ref_boundary_mats(base_grp, face_quad_grp):
        from modepy import faces_for_shape, face_normal

        faces = faces_for_shape(base_grp.shape)
        # NOTE: assumes affine cells (normal components constant)
        face_normals = [face_normal(face) for face in faces]
        # NOTE: assumes same quadrature rule on all faces
        face_quad_weights = face_quad_grp.weights
        nfaces = len(faces)

        return actx.freeze(
            actx.from_numpy(
                np.asarray(
                    [np.diag(
                        np.concatenate([face_quad_weights*face_normals[i][dim]
                                        for i in range(nfaces)])
                    ) for dim in range(base_grp.dim)]
                )
            )
        )

    return get_ref_boundary_mats(base_element_group, face_quad_element_group)


def hybridized_sbp_operators(
        actx: ArrayContext,
        face_element_group, face_quad_element_group,
        vol_element_group, vol_quad_element_group, dtype):
    """todo.
    """
    @keyed_memoize_in(
        actx, hybridized_sbp_operators,
        lambda face_grp, quad_face_grp, vol_grp, quad_vol_grp: (
            face_grp.discretization_key(),
            quad_face_grp.discretization_key(),
            vol_grp.discretization_key(),
            quad_vol_grp.discretization_key()
        )
    )
    def get_hybridized_sbp_mats(face_grp, quad_face_grp, vol_grp, quad_vol_grp):
        from modepy import vandermonde
        from meshmode.discretization.poly_element import diff_matrices

        vand = vandermonde(vol_grp.basis_obj().functions, vol_grp.unit_nodes)
        vdm_inv = np.linalg.inv(vand)
        mass_mat = actx.to_numpy(
            thaw(quadrature_based_mass_matrix(actx, vol_grp, quad_vol_grp), actx)
        )
        p_mat = actx.to_numpy(
            thaw(
                quadrature_based_l2_projection_matrix(actx, vol_grp, quad_vol_grp),
                actx
            )
        )
        vf_mat = actx.to_numpy(
            thaw(
                surface_quadrature_interpolation_matrix(actx, vol_grp,
                                                        quad_face_grp,
                                                        dtype=dtype),
                actx
            )
        )
        b_mats = actx.to_numpy(
            thaw(boundary_matrices(actx, vol_grp, quad_face_grp), actx)
        )
        q_mats = np.asarray(
            # NOTE: need to use a modal differentiation matrix:
            # D_modal = V^{-1} D_nodal
            [p_mat.T @ mass_mat @ diff_mat @ vdm_inv @ p_mat
                for diff_mat in diff_matrices(vol_grp)]
        )
        e_mat = vf_mat @ p_mat
        q_skew_hybridized = np.asarray(
            [0.5 * np.block([[q_mats[dim] - q_mats[dim].T, e_mat.T @ b_mats[dim]],
                             [-b_mats[dim] @ e_mat, b_mats[dim]]])
             for dim in range(vol_grp.dim)]
        )
        return actx.freeze(actx.from_numpy(q_skew_hybridized))

    return get_hybridized_sbp_mats(
        face_element_group, face_quad_element_group,
        vol_element_group, vol_quad_element_group
    )


def _apply_inverse_sbp_mass_operator(
        dcoll: DiscretizationCollection, dd_out, dd_in, vec):
    if isinstance(vec, np.ndarray):
        return obj_array_vectorize(
            lambda vi: _apply_inverse_sbp_mass_operator(dcoll,
                                                        dd_out,
                                                        dd_in, vi), vec
        )

    from grudge.geometry import area_element

    if dd_out != dd_in:
        raise ValueError(
            "Cannot compute inverse of a mass matrix mapping "
            "between different element groups; inverse is not "
            "guaranteed to be well-defined"
        )

    actx = vec.array_context
    discr = dcoll.discr_from_dd(dd_in)
    inv_area_elements = 1./area_element(actx, dcoll, dd=dd_in)

    return DOFArray(
        actx,
        data=tuple(
            # Based on https://arxiv.org/pdf/1608.03836.pdf
            # true_Minv ~ ref_Minv * ref_M * (1/jac_det) * ref_Minv
            actx.einsum("ei,ij,ej->ei",
                        jac_inv,
                        quadrature_based_inverse_mass_matrix(actx,
                                                             element_group=grp),
                        vec_i,
                        arg_names=("mass_mat", "jac", "vec"),
                        tagged=(FirstAxisIsElementsTag(),))

            for grp, jac_inv, vec_i in zip(discr.groups, inv_area_elements, vec)
        )
    )


def inverse_sbp_mass(dcoll: DiscretizationCollection, vec):
    """todo.
    """
    return _apply_inverse_sbp_mass_operator(
        dcoll, dof_desc.DD_VOLUME, dof_desc.DD_VOLUME, vec
    )


def sbp_lifting_matrix(
        actx: ArrayContext, face_element_group, vol_element_group, dtype):
    """todo.
    """
    @keyed_memoize_in(
        actx, sbp_lifting_matrix,
        lambda face_grp, vol_grp: (face_grp.discretization_key(),
                                   vol_grp.discretization_key()))
    def get_ref_sbp_lifting_mat(face_grp, vol_grp):
        nfaces = vol_grp.mesh_el_group.nfaces
        assert face_grp.nelements == nfaces * vol_grp.nelements

        matrix = np.empty(
            (vol_grp.nunit_dofs,
            nfaces,
            face_grp.nunit_dofs),
            dtype=dtype
        )

        from modepy import vandermonde, faces_for_shape

        vol_basis = vol_grp.basis_obj()
        faces = faces_for_shape(vol_grp.shape)
        # NOTE: Assumes same quadrature rule on each face
        face_quadrature = face_grp.quadrature_rule()

        for iface, face in enumerate(faces):
            mapped_nodes = face.map_to_volume(face_quadrature.nodes)
            Vf = vandermonde(vol_basis.functions, mapped_nodes)
            matrix[:, iface, :] = Vf.T @ np.diag(face_quadrature.weights)

        return actx.freeze(actx.from_numpy(matrix))

    return get_ref_sbp_lifting_mat(face_element_group, vol_element_group)


def _apply_sbp_lift_operator(dcoll: DiscretizationCollection, dd, vec):
    if isinstance(vec, np.ndarray):
        return obj_array_vectorize(
            lambda vi: _apply_sbp_lift_operator(dcoll, dd, vi), vec
        )

    from grudge.geometry import area_element

    volm_discr = dcoll.discr_from_dd(dof_desc.DD_VOLUME)
    face_discr = dcoll.discr_from_dd(dd)
    dtype = vec.entry_dtype
    actx = vec.array_context

    assert len(face_discr.groups) == len(volm_discr.groups)
    surf_area_elements = area_element(actx, dcoll, dd=dd)

    @memoize_in(actx, (_apply_sbp_lift_operator, "face_lift_knl"))
    def prg():
        t_unit = make_loopy_program(
            [
                "{[iel]: 0 <= iel < nelements}",
                "{[f]: 0 <= f < nfaces}",
                "{[idof]: 0 <= idof < nvol_nodes}",
                "{[jdof]: 0 <= jdof < nface_nodes}"
            ],
            """
            result[iel, idof] = sum(f, sum(jdof, mat[idof, f, jdof]
                                                 * jac_surf[f, iel, jdof]
                                                 * vec[f, iel, jdof]))
            """,
            name="face_lift"
        )
        import loopy as lp
        from meshmode.transform_metadata import (
                ConcurrentElementInameTag, ConcurrentDOFInameTag)
        return lp.tag_inames(t_unit, {
            "iel": ConcurrentElementInameTag(),
            "idof": ConcurrentDOFInameTag()})

    return DOFArray(
        actx,
        data=tuple(
            actx.call_loopy(prg(),
                            mat=sbp_lifting_matrix(
                                actx,
                                face_element_group=afgrp,
                                vol_element_group=vgrp,
                                dtype=dtype
                            ),
                            jac_surf=surf_ae_i.reshape(
                                vgrp.mesh_el_group.nfaces,
                                vgrp.nelements,
                                afgrp.nunit_dofs
                            ),
                            vec=vec_i.reshape(
                                vgrp.mesh_el_group.nfaces,
                                vgrp.nelements,
                                afgrp.nunit_dofs
                            ))["result"]

            for vgrp, afgrp, vec_i, surf_ae_i in zip(volm_discr.groups,
                                                     face_discr.groups,
                                                     vec,
                                                     surf_area_elements)
        )
    )


def sbp_lift_operator(dcoll: DiscretizationCollection, *args):
    """todo.
    """
    if len(args) == 1:
        vec, = args
        dd = dof_desc.DOFDesc("all_faces", dof_desc.DISCR_TAG_BASE)
    elif len(args) == 2:
        dd, vec = args
    else:
        raise TypeError("invalid number of arguments")

    return _apply_sbp_lift_operator(dcoll, dd, vec)
