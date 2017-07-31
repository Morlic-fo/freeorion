from GraphInterface import GraphInterface
import sys

if True:
    import os
    if os.name == 'nt':
        sys.path.append('C:\\Python27\\Lib')
        sys.path.append('C:\\Python27\\Lib\\site-packages')

import networkx as nx
from networkx.algorithms.connectivity import minimum_st_node_cut
from networkx.algorithms.components import connected_components


class NxGraphInterface(GraphInterface):
    """NetworkX implementation of the GraphInterface"""
    def __init__(self):
        self.__graph = nx.Graph()

    @property
    def graph(self):
        return self.__graph

    def get_nodes(self, get_data=False):
        return self.graph.nodes(data=get_data)

    def get_edges(self, nodes=None, get_data=False):
        return self.graph.edges(nbunch=nodes, data=get_data)

    def reset(self):
        self.__graph = nx.Graph()

    def add_node(self, n, attr_dict=None, **kwargs):
        self.graph.add_node(n, attr_dict=attr_dict, **kwargs)

    def add_edge(self, u, v, attr_dict=None, **kwargs):
        self.graph.add_edge(u, v, attr_dict=attr_dict, **kwargs)

    def remove_node(self, n):
        try:
            self.graph.remove_node(n)
        except nx.NetworkXError:
            print >> sys.stderr, "Tried to delete non-existing node from graph"

    def remove_edge(self, u, v):
        try:
            self.graph.remove_edge(u, v)
        except nx.NetworkXError:
            print >> sys.stderr, "Tried to delete non-existing edge from graph"

    def minimum_st_node_cut(self, source, sink, node_weight_function):
        def build_auxiliary_node_connectivity(G):
            # this is the original NetworkX function modified by adding
            # node weights as edge capacities of the auxiliary digraph
            # to use in the minimum s-t-cut
            r"""Creates a directed graph D from an undirected graph G to compute flow
            based node connectivity.

            For an undirected graph G having `n` nodes and `m` edges we derive a
            directed graph D with `2n` nodes and `2m+n` arcs by replacing each
            original node `v` with two nodes `vA`, `vB` linked by an (internal)
            arc in D. Then for each edge (`u`, `v`) in G we add two arcs (`uB`, `vA`)
            and (`vB`, `uA`) in D. Finally we set the attribute capacity = 1 for each
            arc in D [1]_.

            For a directed graph having `n` nodes and `m` arcs we derive a
            directed graph D with `2n` nodes and `m+n` arcs by replacing each
            original node `v` with two nodes `vA`, `vB` linked by an (internal)
            arc (`vA`, `vB`) in D. Then for each arc (`u`, `v`) in G we add one
            arc (`uB`, `vA`) in D. Finally we set the attribute capacity = 1 for
            each arc in D.

            A dictionary with a mapping between nodes in the original graph and the
            auxiliary digraph is stored as a graph attribute: H.graph['mapping'].

            References
            ----------
            .. [1] Kammer, Frank and Hanjo Taubig. Graph Connectivity. in Brandes and
                Erlebach, 'Network Analysis: Methodological Foundations', Lecture
                Notes in Computer Science, Volume 3418, Springer-Verlag, 2005.
                http://www.informatik.uni-augsburg.de/thi/personen/kammer/Graph_Connectivity.pdf

            """
            directed = G.is_directed()

            mapping = {}
            H = nx.DiGraph()

            for i, node in enumerate(G):
                mapping[node] = i
                H.add_node('%dA' % i, id=node)
                H.add_node('%dB' % i, id=node)
                H.add_edge('%dA' % i, '%dB' % i, capacity=node_weight_function(node))

            edges = []
            for (source, target) in G.edges_iter():
                edges.append(('%sB' % mapping[source], '%sA' % mapping[target]))
                if not directed:
                    edges.append(('%sB' % mapping[target], '%sA' % mapping[source]))
            H.add_edges_from(edges, capacity=999999)

            # Store mapping as graph attribute
            H.graph['mapping'] = mapping
            return H

        return minimum_st_node_cut(self.graph, source, sink,
                                   auxiliary=build_auxiliary_node_connectivity(self.graph))

    def shortest_path(self, source, target):
        return nx.shortest_path(self.__graph, source, target)

    def update_node_attributes(self, attr_name, attr_dict=None):
        nx.set_node_attributes(self.__graph, attr_name, attr_dict)
