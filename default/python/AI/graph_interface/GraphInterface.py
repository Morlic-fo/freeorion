class GraphInterface(object):
    """Implementation and library independent graph interface"""
    @property
    def graph(self):
        raise NotImplementedError

    def get_nodes(self, get_data=False):
        raise NotImplementedError

    def get_edges(self, nodes=None, get_data=False):
        raise NotImplementedError

    def reset(self):
        raise NotImplementedError

    def add_node(self, n, attr_dict=None, **kwargs):
        raise NotImplementedError

    def add_edge(self, u, v, attr_dict=None, **kwargs):
        raise NotImplementedError

    def remove_edge(self, u, v):
        raise NotImplementedError

    def remove_node(self, n):
        raise NotImplementedError

    def minimum_st_node_cut(self, source, sink, node_weight_function):
        raise NotImplementedError

    def shortest_path(self, source, target):
        raise NotImplementedError

    def update_node_attributes(self, attr_name, attr_dict=None):
        raise NotImplementedError

    def node_attributes(self, node):
        raise NotImplementedError
