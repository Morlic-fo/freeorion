import freeOrionAIInterface as fo  # pylint: disable=import-error
import AIDependencies
import PlanetUtilsAI
from freeorion_tools import cache_by_turn, tech_is_complete, ppstring, dict_from_map


# TODO: Cache this information for entire game session not per turn
@cache_by_turn
def galaxy_is_sparse():
    """Check if galaxy is sparse.

    A galaxy is considered sparse if early contact with the enemy is unlikely.

    :rtype: bool
    """
    setup_data = fo.getGalaxySetupData()
    avg_empire_systems = setup_data.size / len(fo.allEmpireIDs())
    return (setup_data.monsterFrequency <= fo.galaxySetupOption.low and
            (avg_empire_systems >= 40 or (avg_empire_systems >= 35 and setup_data.shape != fo.galaxyShape.elliptical)))


@cache_by_turn
def get_supply_tech_range():
    """Get the additional supply range provided by completed techs.

    :rtype: int
    """
    return sum(_range for _tech, _range in AIDependencies.supply_range_techs.iteritems() if tech_is_complete(_tech))


@cache_by_turn
def get_system_supply():
    """Get system supply map.

    The supply proection is negative if system is unsupplied.
    In this case, the value denotes the jump to the nearest
    supplied system.

    :return: Supply projection per system.
    :rtype: dict
    """
    # Note: empire.supplyProjections supply projection has one major difference from standard supply calculations-- if
    # the final parameter (obstructed) is False then it intentionally ignores existing obstructions/blockades, so
    # a Sentinel or somesuch might throw it off, That is an area for future improvement
    return fo.getEmpire().supplyProjections(-1-get_potential_supply_distance(), False)


@cache_by_turn
def _all_systems_by_supply_tier():
    """Get planets by distance to supply lines.

    :return: indexed by the negative value of distance to supplied systems, contains list of system ids.
    :rtype: dict
    """
    systems_by_supply_tier = {}
    for sys_id, supply_val in get_system_supply().items():
        systems_by_supply_tier.setdefault(min(0, supply_val), []).append(sys_id)
    print
    print "New Supply Calc:"
    print "Known Systems:", fo.getUniverse().systemIDs
    print "Base Supply:", dict_from_map(fo.getEmpire().systemSupplyRanges)
    print "New Supply connected systems: ",
    print ppstring(PlanetUtilsAI.sys_name_ids(systems_by_supply_tier.get(0, [])))
    print "New First Ring of annexable systems: ",
    print ppstring(PlanetUtilsAI.sys_name_ids(systems_by_supply_tier.get(-1, [])))
    print "New Second Ring of annexable systems: ",
    print ppstring(PlanetUtilsAI.sys_name_ids(systems_by_supply_tier.get(-2, [])))
    print "New Third Ring of annexable systems: ",
    print ppstring(PlanetUtilsAI.sys_name_ids(systems_by_supply_tier.get(-3, [])))
    return systems_by_supply_tier


def get_systems_by_supply_tier(supply_tier=None):
    """Get all systems by distance to supply lines.

    If no supply_tier is specified, return a dict of lists for each distance.

    :param supply_tier: Distance to empire supply lines.
    :type supply_tier: int
    :return: List of system IDs.
    :rtype: list or dict
    """
    if supply_tier is None:
        return _all_systems_by_supply_tier()
    return _all_systems_by_supply_tier().get(supply_tier, [])


@cache_by_turn
def get_potential_supply_distance():
    """Find the maximum possible supply distance for this empire.

    Considers techs, planet size, species.

    :rtype: int
    """
    potential_supply_distance = get_supply_tech_range()
    potential_supply_distance += max(AIDependencies.supply_by_size.values())
    potential_supply_distance += max(AIDependencies.species_supply_range_modifier.values())
    potential_supply_distance += 1  # World tree special
    # TODO: +1 to consider capturing planets with Elevators
    # if foAI.foAIstate.aggression >= fo.aggression.aggressive:
    # potential_supply_distance += 1
    return potential_supply_distance


@cache_by_turn
def get_all_annexable_system_ids():
    """Get all systems considered annexable.

    :return: of system IDs
    :rtype: set
    """
    # TODO: distinguish colony-annexable systems and outpost-annexable systems
    # TODO: Make sure, that update annexable systm got called before
    annexable_system_ids = set()
    systems_by_supply_tier = get_systems_by_supply_tier()
    for jumps in range(0, -1-get_potential_supply_distance(), -1):
        annexable_system_ids.update(systems_by_supply_tier.get(jumps, []))
    return annexable_system_ids


@cache_by_turn
def check_supply():
    """Get fleet suppliable planets.

    :return: suppliable planet IDs
    :rtype: list
    """
    empire = fo.getEmpire()
    fleet_suppliable_system_ids = empire.fleetSupplyableSystemIDs
    fleet_suppliable_planet_ids = PlanetUtilsAI.get_planets_in__systems_ids(fleet_suppliable_system_ids)
    print
    print "    fleet_suppliable_system_ids: %s" % fleet_suppliable_system_ids
    print "    fleet_suppliable_planet_ids: %s" % fleet_suppliable_planet_ids
    print
    print "-------\nEmpire Obstructed Starlanes:"
    print list(empire.obstructedStarlanes())
    return fleet_suppliable_planet_ids


@cache_by_turn
def annexable_systems_old():
    """Find annexable systems.

    :return: set of system IDs indexed by distance to empire supply lines.
    :rtype: dict
    """
    empire = fo.getEmpire()
    empire_id = empire.empireID
    universe = fo.getUniverse()
    potential_supply_distance = get_potential_supply_distance()
    annexable_system_ids_old = {0: set(empire.fleetSupplyableSystemIDs)}
    covered_systems = annexable_system_ids_old[0]
    for distance in xrange(1, potential_supply_distance+1):
        candidates = set()
        for sys_id in annexable_system_ids_old[distance-1]:
            candidates.update(universe.getImmediateNeighbors(sys_id, empire_id))
        print "covered: ", covered_systems
        annexable_system_ids_old[distance] = candidates - covered_systems
        print "annex %d" % distance, annexable_system_ids_old[distance]
        covered_systems.update(annexable_system_ids_old[distance])
    print "First Ring of annexable systems:", ppstring(PlanetUtilsAI.sys_name_ids(annexable_system_ids_old.get(2, [])))
    print "Second Ring of annexable systems:", ppstring(PlanetUtilsAI.sys_name_ids(annexable_system_ids_old.get(3, [])))
    print "Third Ring of annexable systems:", ppstring(PlanetUtilsAI.sys_name_ids(annexable_system_ids_old.get(4, [])))