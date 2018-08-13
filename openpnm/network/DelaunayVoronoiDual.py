import scipy as sp
import scipy.spatial as sptl
import scipy.sparse as sprs
from skimage.filters import rank_order
from openpnm.network import GenericNetwork
from openpnm import topotools
from openpnm.utils import logging
logger = logging.getLogger(__name__)


class DelaunayVoronoiDual(GenericNetwork):
    r"""
    Combined and interconnected Voronoi and Delaunay tessellations

    A Delaunay tessellation is performed on a set of base points then the
    corresponding Voronoi diagram is generated.  Finally, each Delaunay node
    is connected to it's neighboring Voronoi vertices to create interaction
    between the two networks.

    All pores and throats are labelled according to their network (i.e.
    'pore.delaunay'), so they can be each assigned to a different Geometry.

    The dual-nature of this network is meant for modeling transport in the void
    and solid space simultaneously by treating one network (i.e. Delaunay) as
    voids and the other (i.e. Voronoi) as solid.  Interaction such as heat
    transfer between the solid and void can be accomplished via the
    interconnections between the Delaunay and Voronoi nodes.

    Parameters
    ----------
    num_points : integer
        The number of random base points to distribute inside the domain.
        These points will become connected by the Delaunay triangulation.  The
        points will be generated by calling ``generate_base_points`` in
        ``topotools``.

    points : array_like (num_points x 3)
        A list of coordinates for pre-generated points, typically produced
        using ``generate_base_points`` in topotools.  Note that base points
        should extend beyond the domain so that degenerate Voronoi points
        can be trimmed.

    shape : array_like
        The size and shape of the domain used for generating and trimming
        excess points. The coordinates are treated as the outer corner of a
        rectangle [x, y, z] whose opposite corner lies at [0, 0, 0].

        By default, a domain size of [1, 1, 1] is used.  To create a 2D network
        set the Z-dimension to 0.

    name : string
        An optional name for the object to help identify it.  If not given,
        one will be generated.

    project : OpenPNM Project object, optional
        Each OpenPNM object must be part of a *Project*.  If none is supplied
        then one will be created and this Network will be automatically
        assigned to it.  To create a *Project* use ``openpnm.Project()``.

    Examples
    --------
    Points will be automatically generated if none are given:

    >>> import openpnm as op
    >>> net = op.network.DelaunayVoronoiDual(num_points=50, shape=[1, 1, 0])

    The resulting network can be quickly visualized using
    ``opnepnm.topotools.plot_connections``.

    """

    def __init__(self, shape=[1, 1, 1], num_points=None, **kwargs):
        points = kwargs.pop('points', None)
        points = self._parse_points(shape=shape,
                                    num_points=num_points,
                                    points=points)

        # Deal with points that are only 2D...they break tessellations
        if points.shape[1] == 3 and len(sp.unique(points[:, 2])) == 1:
            points = points[:, :2]

        # Perform tessellation
        vor = sptl.Voronoi(points=points)
        self._vor = vor

        # Combine points
        pts_all = sp.vstack((vor.points, vor.vertices))
        Nall = sp.shape(pts_all)[0]

        # Create adjacency matrix in lil format for quick construction
        am = sprs.lil_matrix((Nall, Nall))
        for ridge in vor.ridge_dict.keys():
            # Make Delaunay-to-Delauny connections
            [am.rows[i].extend([ridge[0], ridge[1]]) for i in ridge]
            # Get voronoi vertices for current ridge
            row = vor.ridge_dict[ridge].copy()
            # Index Voronoi vertex numbers by number of delaunay points
            row = [i + vor.npoints for i in row if i > -1]
            # Make Voronoi-to-Delaunay connections
            [am.rows[i].extend(row) for i in ridge]
            # Make Voronoi-to-Voronoi connections
            row.append(row[0])
            [am.rows[row[i]].append(row[i+1]) for i in range(len(row)-1)]

        # Finalize adjacency matrix by assigning data values
        am.data = am.rows  # Values don't matter, only shape, so use 'rows'
        # Convert to COO format for direct acces to row and col
        am = am.tocoo()
        # Extract rows and cols
        conns = sp.vstack((am.row, am.col)).T

        # Convert to sanitized adjacency matrix
        am = topotools.conns_to_am(conns)
        # Finally, retrieve conns back from am
        conns = sp.vstack((am.row, am.col)).T

        # Translate adjacency matrix and points to OpenPNM format
        coords = sp.around(pts_all, decimals=10)
        if coords.shape[1] == 2:  # Make points back into 3D if necessary
            coords = sp.vstack((coords.T, sp.zeros((coords.shape[0], )))).T
        super().__init__(conns=conns, coords=coords, **kwargs)

        # Label all pores and throats by type
        self['pore.delaunay'] = False
        self['pore.delaunay'][0:vor.npoints] = True
        self['pore.voronoi'] = False
        self['pore.voronoi'][vor.npoints:] = True
        # Label throats between Delaunay pores
        self['throat.delaunay'] = False
        Ts = sp.all(self['throat.conns'] < vor.npoints, axis=1)
        self['throat.delaunay'][Ts] = True
        # Label throats between Voronoi pores
        self['throat.voronoi'] = False
        Ts = sp.all(self['throat.conns'] >= vor.npoints, axis=1)
        self['throat.voronoi'][Ts] = True
        # Label throats connecting a Delaunay and a Voronoi pore
        self['throat.interconnect'] = False
        Ts = self.throats(labels=['delaunay', 'voronoi'], mode='not')
        self['throat.interconnect'][Ts] = True

        # Trim all pores that lie outside of the specified domain
        self._trim_external_pores(shape=shape)

    @property
    def tri(self):
        if not hasattr(self, '_tri'):
            points = self._vor.points
            self._tri = sptl.Delaunay(points=points)
        return self._tri

    @property
    def vor(self):
        return self._vor

    def _trim_external_pores(self, shape):
        r'''
        '''
        # Find all pores within the domain
        Ps = topotools.isoutside(coords=self['pore.coords'], shape=shape)
        self['pore.external'] = False
        self['pore.external'][Ps] = True

        # Find which internal pores are delaunay
        Ps = (~self['pore.external'])*self['pore.delaunay']

        # Find all pores connected to an internal delaunay pore
        Ps = self.find_neighbor_pores(pores=Ps, include_input=True)

        # Mark them all as keepers
        self['pore.keep'] = False
        self['pore.keep'][Ps] = True

        # Trim all bad pores
        topotools.trim(network=self, pores=~self['pore.keep'])

        # Now label surface pores
        self['pore.surface'] = False
        self['pore.surface'] = self['pore.delaunay']*self['pore.external']

        # Label Voronoi pores on surface
        Ps = self.find_neighbor_pores(pores=self.pores('surface'))
        Ps = self['pore.voronoi']*self.tomask(pores=Ps)
        self['pore.surface'][Ps] = True

        # Label Voronoi and interconnect throats on surface
        self['throat.surface'] = False
        Ps = self.pores('surface')
        Ts = self.find_neighbor_throats(pores=Ps, mode='xnor')
        self['throat.surface'][Ts] = True

        # Trim throats between Delaunay surface pores
        Ps = self.pores(labels=['surface', 'delaunay'], mode='xnor')
        Ts = self.find_neighbor_throats(pores=Ps, mode='xnor')
        topotools.trim(network=self, throats=Ts)

        # Move Delaunay surface pores to centroid of Voronoi facet
        Ps = self.pores(labels=['surface', 'delaunay'], mode='xnor')
        for P in Ps:
            Ns = self.find_neighbor_pores(pores=P)
            Ns = Ps = self['pore.voronoi']*self.tomask(pores=Ns)
            coords = sp.mean(self['pore.coords'][Ns], axis=0)
            self['pore.coords'][P] = coords

        self['pore.internal'] = ~self['pore.surface']
        Ps = self.pores('internal')
        Ts = self.find_neighbor_throats(pores=Ps, mode='xnor')
        self['throat.internal'] = False
        self['throat.internal'][Ts] = True

        # Clean-up
        del self['pore.external']
        del self['pore.keep']

    def find_throat_facets(self, throats=None):
        r"""
        Finds the indicies of the Voronoi nodes that define the facet or
        ridge between the Delaunay nodes connected by the given throat.

        Parameters
        ----------
        throats : array_like
            The throats whose facets are sought.  The given throats should be
            from the 'delaunay' network. If no throats are specified, all
            'delaunay' throats are assumed.

        Notes
        -----
        The method is not well optimized as it scans through each given throat
        inside a for-loop, so it could be slow for large networks.

        """
        if throats is None:
            throats = self.throats('delaunay')
        temp = []
        tvals = self['throat.interconnect'].astype(int)
        am = self.create_adjacency_matrix(weights=tvals, fmt='lil',
                                          drop_zeros=True)
        for t in throats:
            P12 = self['throat.conns'][t]
            Ps = list(set(am.rows[P12][0]).intersection(am.rows[P12][1]))
            temp.append(Ps)
        return sp.array(temp, dtype=object)

    def find_pore_hulls(self, pores=None):
        r"""
        Finds the indices of the Voronoi nodes that define the convex hull
        around the given Delaunay nodes.

        Parameters
        ----------
        pores : array_like
            The pores whose convex hull are sought.  The given pores should be
            from the 'delaunay' network.  If no pores are given, then the hull
            is found for all 'delaunay' pores.

        Notes
        -----
        This metod is not fully optimized as it scans through each pore in a
        for-loop, so could be slow for large networks.
        """
        if pores is None:
            pores = self.pores('delaunay')
        temp = []
        tvals = self['throat.interconnect'].astype(int)
        am = self.create_adjacency_matrix(weights=tvals, fmt='lil',
                                          drop_zeros=True)
        for p in pores:
            Ps = am.rows[p]
            temp.append(Ps)
        return sp.array(temp, dtype=object)

    def _parse_points(self, shape, points, num_points):
        # Deal with input arguments
        if points is None:
            if num_points is None:
                raise Exception('Must specify either "points" or "num_points"')
            points = topotools.generate_base_points(num_points=num_points,
                                                    domain_size=shape,
                                                    reflect=True)
        else:
            # Should we check to ensure that points are reflected?
            points = sp.array(points)

        # Deal with points that are only 2D...they break Delaunay
        if points.shape[1] == 3 and len(sp.unique(points[:, 2])) == 1:
            points = points[:, :2]

        return points

    def add_boundary_pores(self, label, offset):
        r"""
        """
        offset = sp.array(offset)
        if (offset.size == 3) or (offset.shape == ()):
            Ps = self.pores(label)
            topotools.add_boundary_pores(network=self, pores=Ps)
