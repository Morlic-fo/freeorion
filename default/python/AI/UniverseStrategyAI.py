# add python standard library path to allow install included external libraries
# Not sure if there is any clean and safe way to find the relevant paths automatically
# given we use a custom python installation in freeorion.
# For development purposes, just hardcoding the path for now...
import freeOrionAIInterface as fo
import FreeOrionAI as foAI
from freeorion_tools import print_error
from graph_interface import Graph


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
            print "fo__N__ %s\n" % str(node)  # (n, data_dict) tuple
        for edge in self.get_edges(get_data=True):
            print "fo__E__", edge  # (u, v, data_dict) tuple


__universe_graph = UniverseGraph()


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
    g.dump()


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


