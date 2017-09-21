from heapq import heappush, heappop
from collections import namedtuple

import freeOrionAIInterface as fo
from turn_state import state


# TODO Allow short idling in system to refuel
# TODO Consider additional optimizations:
#    - Check if universe.shortestPath complies with fuel mechanics before running
#      pathfinder (seemingly not worth it - we have exact heuristic in that case)
#    - Cut off branches that can't reach target due to supply distance
#    - For large graphs, check existance on a simplified graph (use single node to represent supply clusters)
#    - For large graphs, check if there are any must-visit nodes (e.g. only possible resupplying system),
#      then try to find the shortest path between those and start/target.
def find_path_with_resupply(start, target, fleet_id):
    """Find the shortest possible path between two systems that complies with FreeOrion fuel mechanics.

     If the fleet can travel the shortest possible path between start and target system, then return that path.
     Otherwise, find the shortest possible detour including refueling.


     The core algorithm is a modified A* with the universe.shortestPathDistance as heuristic.
     While searching for a path, keep track of the fleet's fuel. Compared to standard A*/dijkstra,
     nodes are locked only for a certain minimum level of fuel. If a longer path yields a higher fuel
     level at a given system, then that path considered for pathfinding and added to the queue.


    :param start: start system id
    :type start: int
    :param target:  target system id
    :type target: int
    :param fleet_id: fleet to find the path for
    :type fleet_id: int
    :return: shortest possible path including resupply-deroutes in the form of system ids
             including both start and target system
    :rtype: tuple[int]
    """
    path_information = namedtuple('path_information', ['distance', 'fuel', 'path'])
    universe = fo.getUniverse()
    empire_id = fo.empireID()
    fleet = universe.getFleet(fleet_id)
    supplied_systems = set(fo.getEmpire().fleetSupplyableSystemIDs)

    # make sure the target is connected to the start system
    shortest_possible_path_distance = universe.shortestPathDistance(start, target)
    if shortest_possible_path_distance == -1:
        return None

    start_fuel = fleet.maxFuel if start in supplied_systems else fleet.fuel

    # We have 1 free jump from supplied system into unsupplied systems.
    # Thus, the target system must be at most maxFuel + 1 jumps away
    # in order to reach the system under standard conditions.
    # In some edge cases, we may have more supply here than what the
    # supply graph suggests. For example, we could have recently refueled
    # in a system that is now blockaded by an enemy or we have found
    # the refuel special.
    target_distance_from_supply = -state.get_systems_by_supply_tier(target)
    if (fleet.maxFuel + 1 < target_distance_from_supply and
            universe.jumpDistance(start, target) > start_fuel):
        # can't possibly reach this system
        return None

    # initialize data structures
    path_cache = {}
    queue = []

    # add starting system to queue
    heappush(queue, (shortest_possible_path_distance, path_information(distance=0, fuel=start_fuel, path=(start,)),
                     start))

    while queue:
        # get next system u with path information
        (_, path_info, current) = heappop(queue)

        # did we reach the target?
        if current == target:
            return path_info

        # add information about how we reached here to the cache
        path_cache.setdefault(current, []).append(path_info)

        # check if we have enough fuel to travel to neighbors
        if path_info.fuel < 1:
            continue

        # add neighboring systems to the queue if the resulting path
        # is either shorter or offers more fuel than the other paths
        # which we already found to those systems
        for neighbor in universe.getImmediateNeighbors(current, empire_id):
            new_dist = path_info.distance + universe.linearDistance(current, neighbor)
            new_fuel = fleet.maxFuel if neighbor in supplied_systems else path_info.fuel - 1
            if all((new_dist < dist or new_fuel > fuel) for dist, fuel, _ in path_cache.get(neighbor, [])):
                predicted_distance = new_dist + universe.shortestPathDistance(neighbor, target)
                if predicted_distance > max(2*shortest_possible_path_distance, shortest_possible_path_distance+5):
                    # do not consider unreasonable long paths
                    continue
                heappush(queue, (predicted_distance, path_information(new_dist, new_fuel, path_info.path + (neighbor,)),
                                 neighbor))

    # no path exists, not even if we refuel on the way
    return None


def run(s1, s2):
    universe = fo.getUniverse()
    fleets = universe.fleetIDs
    f = [fid for fid in universe.getSystem(126).fleetIDs][0]
    if not (s1 and s2):
        ss = universe.systemIDs
        s1 = ss[0]
        s2 = ss[1]
    path_info = find_path_with_resupply(s1, s2, f)
    print map(universe.getSystem, path_info.path)
