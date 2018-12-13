import copy
import sys
from functools import wraps

import freeOrionAIInterface as fo
import FreeOrionAI as foAI
from freeorion_tools import print_error
from graph_interface import Graph


# If set to true, this flag will deepcopy the universe graph before
# function calls decorated with @alters_and_restores_universe_graph
# and assert that the function leaves the universe graph intact.
# As deepcopying is generally a very expensive operation, this flag
# should be set to False after algorithms are verified to work correctly.
# If new functions are added that require the @alters_and_restores_universe_graph
# decorator, then this flag should be set to True to verify the correctness
# of the implementation.
DEBUG_UNIVERSE_GRAPH_CONSISTENCY = True


class BrokenUniverseGraphException(Exception):

    def __init__(self, fnc_name=""):
        self.message = "Function %s broke the UniverseGraph instance" % fnc_name
        print_error(self.message)


class UniverseGraph(Graph):

    def __init__(self):
        super(UniverseGraph, self).__init__()
        self._last_update = -1

    @property
    def graph(self):
        if fo.currentTurn() != self._last_update:
            # calculate an updated universe graph for this turn
            self._last_update = fo.currentTurn()
            self.update()
        return super(UniverseGraph, self).graph

    def update(self):
        self.__create_universe_graph()

    def __create_universe_graph(self):
        """Build a networkx-Graph object that represents the known universe.

        The nodes of the returned graph are the known systems in the universe.
        The edges of the returned graph are the known starlanes in the universe.

        Additional information may be stored in and associated with the nodes/edges
        reflecting the AI's knowledge about ownership, visibility, threat etc.

        :rtype: networkx.Graph
        """
        self.reset()  # reset to an empty graph

        universe = fo.getUniverse()
        empire_id = fo.empireID()
        for system_id in universe.systemIDs:
            system = universe.getSystem(system_id)
            if not system:
                continue
            owners = set()
            for planet_id in system.planetIDs:
                planet = universe.getPlanet(planet_id)
                if not planet or planet.unowned:
                    continue
                owners.add(planet.owner)

            node_dict = {
                'pos': (system.x, system.y),
                'name':  system.name,
            }
            # do not add empty/False attributes to the dict
            if owners:
                node_dict['owners'] = tuple(owners)
            if system_id in foAI.foAIstate.exploredSystemIDs:
                node_dict['explored'] = True
            if system_id == foAI.foAIstate._AIstate__origin_home_system_id:
                node_dict['home_system'] = True
            if universe.getVisibilityTurnsMap(system_id, empire_id).get(fo.visibility.partial, -9999) > -1:
                node_dict['scanned'] = True

            self.add_node(system_id, attr_dict=node_dict)

            for neighbor in foAI.foAIstate.systemStatus[system_id].get('neighbors', []):
                self.add_edge(system_id, neighbor, distance=universe.linearDistance(system_id, neighbor))

    def owned_nodes(self):
        return {n for n, data in self.get_nodes(get_data=True) if fo.empireID() in data.get('owners', [])}

    def unexplored_nodes(self):
        return {n for n, data in self.get_nodes(get_data=True) if not data.get('explored', False)}

    def enemy_nodes(self):
        return {n for n, data in self.get_nodes(get_data=True)
                if any(owner != fo.empireID() for owner in data.get('owners', []))}

    def dump(self):
        # Dumping a large graph into a single line will exceed the maximum line length.
        # Instead, dump one line at a time. For easier parsing, add a prefix to each line.
        for node in self.get_nodes(get_data=True):
            print "fo__N__ %s" % str(node)  # (n, data_dict) tuple
        for edge in self.get_edges(get_data=True):
            print "fo__E__", edge  # (u, v, data_dict) tuple


__universe_graph = UniverseGraph()


def alters_and_restores_universe_graph(function):
    """Decorator to mark functions that alter the universe_graph and restore it on exit.

    Multiple functions in this module require to add or remove nodes or edges
    from the __universe_graph instance. Because deepcopying the entire graph is
    costly, those functions generally work on the original graph instance and then
    will restore the original __universe_graph state.

    If the flag __DEBUG_UNIVERSE_GRAPH_CONSISTENCY is set, then this decorator
    will deepcopy the __universe_graph before the function and then compare this
    original state of the __universe_graph with the state after the function call.

    If discrepencies are found, those are logged and a BrokenUniverseGraphException
    is thrown.
    """
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not DEBUG_UNIVERSE_GRAPH_CONSISTENCY:
            return function(*args, **kwargs)

        original_graph = copy.deepcopy(__universe_graph)

        retval = function(*args, **kwargs)

        original_edges = original_graph.get_edges(get_data=True)
        original_nodes = original_graph.get_nodes(get_data=True)
        new_edges = __universe_graph.get_edges(get_data=True)
        new_nodes = __universe_graph.get_nodes(get_data=True)

        original_nodes = {n: data for (n, data) in original_nodes}
        original_edges = {tuple(sorted((u, v))): data for (u, v, data) in original_edges}
        new_nodes = {n: data for (n, data) in new_nodes}
        new_edges = {tuple(sorted((u, v))): data for (u, v, data) in new_edges}

        broken_graph = False
        # verify edges
        if new_edges != original_edges:
            broken_graph = True
            old_edge_set = set(original_edges.keys())
            new_edge_set = set(new_edges.keys())
            for edge in new_edge_set - old_edge_set:
                print >> sys.stderr, "Function added edge %s to the graph." % str(edge)
            for edge in old_edge_set - new_edge_set:
                print >> sys.stderr, "Function deleted edge %s from the graph." % str(edge)
            for edge in old_edge_set.intersection(new_edge_set):
                old_attr = original_edges[edge]
                new_attr = new_edges.get(edge, {})
                if old_attr != new_attr:
                    print >> sys.stderr, "Function altered edge %s attribute dict from %s to %s" % (
                        str(edge), old_attr, new_attr)

        # verify nodes
        if new_nodes != original_nodes:
            broken_graph = True
            old_node_set = set(original_nodes.keys())
            new_node_set = set(new_nodes.keys())
            for node in new_node_set - old_node_set:
                print >> sys.stderr, "Function added node %s to the graph." % str(node)
            for node in old_node_set - new_node_set:
                print >> sys.stderr, "Function deleted node %s from the graph." % str(node)
            for node in old_node_set.intersection(new_node_set):
                old_attr = original_nodes[node]
                new_attr = new_nodes.get(node, {})
                if old_attr != new_attr:
                    print >> sys.stderr, "Function altered node %s attribute dict from %s to %s" % (
                        node, old_attr, new_attr)
        if broken_graph:
            raise BrokenUniverseGraphException(fnc_name=function.__name__)

        return retval
    return wrapper


def get_universe_graph():
    return __universe_graph


def dump_universe_graph():
    print "Dumping Universe Graph, EmpireID: %d" % fo.empireID()
    import copy
    __universe_graph.update()
    g = copy.deepcopy(__universe_graph)  # TODO just temporary

    border_systems = find_defensive_positions_min_cut(1, 0)
    middle_systems = find_defensive_positions_min_cut(1, .99)
    offensive_systems = find_defensive_positions_min_cut(1, 100)

    g.update_node_attributes('border_system',    {n: True for n in border_systems})
    g.update_node_attributes('expansion_system', {n: True for n in middle_systems})
    g.update_node_attributes('offensive_system', {n: True for n in offensive_systems})

    inner_systems = find_inner_systems(g)
    g.update_node_attributes('inner_system',     {n: True for n in inner_systems})

    g.dump()


@alters_and_restores_universe_graph
def find_defensive_positions_min_cut(weight_owned, weight_enemy):
    SINK = 999998
    SOURCE = 999999
    edges = [(SINK, node) for node in __universe_graph.enemy_nodes() | __universe_graph.unexplored_nodes()]
    edges.extend([(SOURCE, node) for node in __universe_graph.owned_nodes()])

    # to avoid working on an expensive (deep)copy of the universe graph, we add edges
    # and nodes to the existing universe graph and remove them when exiting this function.
    __universe_graph.add_node(SINK)
    __universe_graph.add_node(SOURCE)
    for (u, v) in edges:
        __universe_graph.add_edge(u, v)
        __universe_graph.add_edge(v, u)

    def weight_fnc(n):
        distance_to_owned = len(__universe_graph.shortest_path(n, SOURCE)) - 1
        distance_to_enemy = len(__universe_graph.shortest_path(n, SINK)) - 1
        return (weight_owned*distance_to_owned - weight_enemy*distance_to_enemy)**2

    try:
        # note that the finally-block is executed even if we exit the function using a return statement
        return __universe_graph.minimum_st_node_cut(SOURCE, SINK, weight_fnc)
    except Exception as e:
        print_error(e)
        return set()
    finally:
        # remove the previously added nodes and edges
        __universe_graph.remove_node(SINK)
        __universe_graph.remove_node(SOURCE)
        for (u, v) in edges:
            __universe_graph.remove_edge(u, v)
            __universe_graph.remove_edge(v, u)


@alters_and_restores_universe_graph
def find_inner_systems(g=None):
    """Find inner systems of the empire.

    Inner systems are defined as systems that are separated from all enemy systems
    and all unscanned systems by at least 1 empire owned system.

    :return: ids of inner systems
    :rtype: set[int]
    """
    # The basic algorithm is as follows:
    # 1. remove all "border systems" from the universe graph, making sure to keep track of deleted nodes and edges
    # 2. find and loop over all connected components of the resulting graph
    # 3. If no system in a connected component is either owned by an enemy or is unscanned, this is an inner system set
    # 4. Finally, restore the original universe graph and return the found set of inner systems
    if g is None:
        g = __universe_graph
    border_systems = {node: data for node, data in g.get_nodes(get_data=True)
                      if data.get('border_system', False)}
    print border_systems
    edges_removed = g.get_edges(nodes=border_systems.keys(), get_data=True)
    for (u, v, data) in edges_removed:
        g.remove_edge(u, v)
    for n in border_systems.keys():
        g.remove_node(n)

    # we use a try-except-finally block to make sure the original graph is always restored when exiting this function.
    try:
        empire_id = fo.empireID()
        inner_systems = set()
        connected_components = g.find_connected_components()
        for subnodelist in connected_components:
            for n in subnodelist:
                attr_dict = g.node_attributes(n)
                owners = attr_dict.get('owners', [])
                if empire_id in owners:
                    # can shortcut here: Owned system was not a border system, i.e. must be inner system
                    # if 1 system of the subgraph is an inner system, then all systems are.
                    inner_systems.update(subnodelist)
                    break
                if owners:
                    # someone else owns a system in this subset. Can't be an inner system.
                    break
                if not attr_dict.get('scanned', False):
                    # we did not scout this system yet. So can't be an inner system
                    break
            else:
                # no system in this subset was owned by another empire and all systems were scanned
                # by our definition, this is an inner system.
                inner_systems.update(subnodelist)
        return inner_systems
    except Exception as e:
        print_error(e)
        return set()
    finally:
        # restore the previously added nodes and edges
        for node, data in border_systems.items():
            g.add_node(node, data)
        for (u, v, data) in edges_removed:
            g.add_edge(u, v, data)
