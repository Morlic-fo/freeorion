import math
import random
import sys

import freeOrionAIInterface as fo
import AIstate
import BuildingsAI
from character.character_module import Aggression
import FleetUtilsAI
import FreeOrionAI as foAI
import PlanetUtilsAI
import PriorityAI
import ColonisationAI
import MilitaryAI
import ShipDesignAI
import ShipyardAI
import CombatRatingsAI
from turn_state import state

from EnumsAI import (PriorityType, EmpireProductionTypes, MissionType, get_priority_production_types,
                     FocusType, ShipRoleType)
from freeorion_tools import dict_from_map, ppstring, AITimer
from common.print_utils import Table, Sequence, Text
from AIDependencies import INVALID_ID
from ProductionQueueAI import SHIP, BUILDING, ProductionPriority as Priority

_best_military_design_rating_cache = {}  # indexed by turn, values are rating of the military design of the turn
_design_cost_cache = {0: {(-1, -1): 0}}  # outer dict indexed by cur_turn ; inner dict indexed by (design_id, pid)

_design_cache = {}  # dict of tuples (rating,pid,designID,cost) sorted by rating and indexed by priority type

_CHAT_DEBUG = False
ARB_LARGE_NUMBER = 1e4


def find_best_designs_this_turn():
    """Calculate the best designs for each ship class available at this turn."""
    design_timer = AITimer('ShipDesigner')
    design_timer.start('Updating cache for new turn')
    ShipDesignAI.Cache.update_for_new_turn()
    _design_cache.clear()

    # TODO Dont use PriorityType but introduce more reasonable Enum
    designers = [
        ('Orbital Invasion', PriorityType.PRODUCTION_ORBITAL_INVASION, ShipDesignAI.OrbitalTroopShipDesigner),
        ('Invasion', PriorityType.PRODUCTION_INVASION, ShipDesignAI.StandardTroopShipDesigner),
        ('Orbital Colonization', PriorityType.PRODUCTION_ORBITAL_COLONISATION,
         ShipDesignAI.OrbitalColonisationShipDesigner),
        ('Colonization', PriorityType.PRODUCTION_COLONISATION, ShipDesignAI.StandardColonisationShipDesigner),
        ('Orbital Outposter', PriorityType.PRODUCTION_ORBITAL_OUTPOST, ShipDesignAI.OrbitalOutpostShipDesigner),
        ('Outposter', PriorityType.PRODUCTION_OUTPOST, ShipDesignAI.StandardOutpostShipDesigner),
        ('Orbital Defense', PriorityType.PRODUCTION_ORBITAL_DEFENSE, ShipDesignAI.OrbitalDefenseShipDesigner),
        ('Scouts', PriorityType.PRODUCTION_EXPLORATION, ShipDesignAI.ScoutShipDesigner),
    ]

    for timer_name, priority_type, designer in designers:
        design_timer.start(timer_name)
        _design_cache[priority_type] = designer().optimize_design()
    best_military_stats = ShipDesignAI.MilitaryShipDesigner().optimize_design()
    best_carrier_stats = ShipDesignAI.CarrierShipDesigner().optimize_design()
    best_stats = best_military_stats + best_carrier_stats
    best_stats.sort(reverse=True)
    _design_cache[PriorityType.PRODUCTION_MILITARY] = best_stats
    design_timer.start('Krill Spawner')
    ShipDesignAI.KrillSpawnerShipDesigner().optimize_design()  # just designing it, building+mission not supported yet
    if fo.currentTurn() % 10 == 0:
        design_timer.start('Printing')
        ShipDesignAI.Cache.print_best_designs()
    design_timer.stop_print_and_clear()


def get_design_cost(design, pid):  # TODO: Use new framework
    """Find and return the design_cost of the specified design on the specified planet.

    :param design:
    :type design: fo.shipDesign
    :param pid: planet id
    :type pid: int
    :return: cost of the design
    """
    cur_turn = fo.currentTurn()
    if cur_turn in _design_cost_cache:
        cost_cache = _design_cost_cache[cur_turn]
    else:
        _design_cost_cache.clear()
        cost_cache = {}
        _design_cost_cache[cur_turn] = cost_cache
    loc_invariant = design.costTimeLocationInvariant
    if loc_invariant:
        loc = INVALID_ID
    else:
        loc = pid
    return cost_cache.setdefault((design.id, loc), design.productionCost(fo.empireID(), pid))


def cur_best_military_design_rating():
    """Find and return the default combat rating of our best military design.

    :return: float: rating of the best military design
    """
    current_turn = fo.currentTurn()
    if current_turn in _best_military_design_rating_cache:
        return _best_military_design_rating_cache[current_turn]
    priority = PriorityType.PRODUCTION_MILITARY
    if _design_cache.get(priority, None) and _design_cache[priority][0]:
        # the rating provided by the ShipDesigner does not
        # reflect the rating used in threat considerations
        # but takes additional factors (such as cost) into
        # account. Therefore, we want to calculate the actual
        # rating of the design as provided by CombatRatingsAI.
        _, _, _, _, stats = _design_cache[priority][0]
        # TODO: Should this consider enemy stats?
        rating = CombatRatingsAI.ShipCombatStats(stats=stats.convert_to_combat_stats()).get_rating()
        _best_military_design_rating_cache[current_turn] = rating
        return max(rating, 0.001)
    return 0.001


def get_best_ship_info(priority, loc=None):
    """ Returns 3 item tuple: designID, design, buildLocList."""
    if loc is None:
        planet_ids = AIstate.popCtrIDs
    elif isinstance(loc, list):
        planet_ids = set(loc).intersection(AIstate.popCtrIDs)
    elif isinstance(loc, int) and loc in AIstate.popCtrIDs:
        planet_ids = [loc]
    else:  # problem
        return None, None, None
    if priority in _design_cache:
        best_designs = _design_cache[priority]
        if not best_designs:
            return None, None, None

        for design_stats in best_designs:
            top_rating, pid, top_id, cost, stats = design_stats
            if pid in planet_ids:
                break
        valid_locs = [item[1] for item in best_designs if item[0] == top_rating and item[2] == top_id]
        return top_id, fo.getShipDesign(top_id), valid_locs
    else:
        return None, None, None  # must be missing a Shipyard or other orbital (or missing tech)


def get_best_ship_ratings(planet_ids):
    """
    Returns list of [partition, pid, designID, design] sublists, currently only for military ships.

    Since we haven't yet implemented a way to target military ship construction at/near particular locations
    where they are most in need, and also because our rating system is presumably useful-but-not-perfect, we want to
    distribute the construction across the Resource Group and across similarly rated designs, preferentially choosing
    the best rated design/loc combo, but if there are multiple design/loc combos with the same or similar ratings then
    we want some chance of choosing  those alternate designs/locations.

    The approach to this taken below is to treat the ratings akin to an energy to be used in a statistical mechanics
    type partition function. 'tally' will compute the normalization constant.
    So first go through and calculate the tally as well as convert each individual contribution to
    the running total up to that point, to facilitate later sampling.  Then those running totals are
    renormalized by the final tally, so that a later random number selector in the range [0,1) can be
    used to select the chosen design/loc.

    :param planet_ids: list of planets ids.
    :type planet_ids: list|set|tuple
    :rtype: list
    """
    priority = PriorityType.PRODUCTION_MILITARY
    planet_ids = set(planet_ids).intersection(ColonisationAI.empire_shipyards)

    if priority in _design_cache:
        build_choices = _design_cache[priority]
        loc_choices = [[rating, pid, design_id, fo.getShipDesign(design_id)]
                       for (rating, pid, design_id, cost, stats) in build_choices if pid in planet_ids]
        if not loc_choices:
            return []
        best_rating = loc_choices[0][0]
        tally = 0
        ret_val = []
        for rating, pid, design_id, design in loc_choices:
            if rating < 0.7 * best_rating:
                break
            p = math.exp(10 * (rating/best_rating - 1))
            tally += p
            ret_val.append([tally, pid, design_id, design])
        for item in ret_val:
            item[0] /= tally
        return ret_val
    else:
        return []


def generate_production_orders():
    """generate production orders"""
    # first check ship designs
    # next check for buildings etc that could be placed on queue regardless of locally available PP
    # next loop over resource groups, adding buildings & ships
    _print_production_queue()
    universe = fo.getUniverse()
    capital_id = PlanetUtilsAI.get_capital()
    if capital_id is None or capital_id == INVALID_ID:
        homeworld = None
        capital_system_id = None
    else:
        homeworld = universe.getPlanet(capital_id)
        capital_system_id = homeworld.systemID
    print "Production Queue Management:"
    empire = fo.getEmpire()
    production_queue = empire.productionQueue
    total_pp = empire.productionPoints
    # prodResPool = empire.getResourcePool(fo.resourceType.industry)
    # available_pp = dict_from_map(production_queue.available_pp(prodResPool))
    # allocated_pp = dict_from_map(production_queue.allocated_pp)
    # objectsWithWastedPP = production_queue.objectsWithWastedPP(prodResPool)
    current_turn = fo.currentTurn()
    print
    print "  Total Available Production Points: %s" % total_pp

    claimed_stars = foAI.foAIstate.misc.get('claimedStars', {})
    if claimed_stars == {}:
        for sType in AIstate.empireStars:
            claimed_stars[sType] = list(AIstate.empireStars[sType])
        for sys_id in set(AIstate.colonyTargetedSystemIDs + AIstate.outpostTargetedSystemIDs):
            t_sys = universe.getSystem(sys_id)
            if not t_sys:
                continue
            claimed_stars.setdefault(t_sys.starType, []).append(sys_id)

    if current_turn == 1 and len(AIstate.opponentPlanetIDs) == 0:
        best_design_id, _, build_choices = get_best_ship_info(PriorityType.PRODUCTION_EXPLORATION)
        if best_design_id is not None:
            for _ in range(3):
                foAI.foAIstate.production_queue_manager.enqueue_item(SHIP, best_design_id, build_choices[0],
                                                                     Priority.ship_scout * Priority.emergency_factor)
            fo.updateProductionQueue()

    print "Buildings present on all owned planets:"
    for pid in list(AIstate.popCtrIDs) + list(AIstate.outpostIDs):
        planet = universe.getPlanet(pid)
        if planet:
            print "%30s: %s" % (planet.name, [universe.getBuilding(bldg).name for bldg in planet.buildingIDs])
    print

    if homeworld:
        table = Table([
            Text('Id', description='Building id'),
            Text('Name'),
            Text('Type'),
            Sequence('Tags'),
            Sequence('Specials'),
            Text('Owner Id'),
        ], table_name='Buildings present at empire Capital in Turn %d' % fo.currentTurn())

        for building_id in homeworld.buildingIDs:
            building = universe.getBuilding(building_id)

            table.add_row((
                building_id,
                building.name,
                "_".join(building.buildingTypeName.split("_")[-2:]),
                sorted(building.tags),
                sorted(building.specials),
                building.owner
            ))

        table.print_table()
        print

    max_defense_portion = foAI.foAIstate.character.max_defense_portion()
    if foAI.foAIstate.character.check_orbital_production():
        sys_orbital_defenses = {}
        queued_defenses = {}
        defense_allocation = 0.0
        target_orbitals = foAI.foAIstate.character.target_number_of_orbitals()
        print "Orbital Defense Check -- target Defense Orbitals: ", target_orbitals
        for element in production_queue:
            if (element.buildType == EmpireProductionTypes.BT_SHIP) and (
                        foAI.foAIstate.get_ship_role(element.designID) == ShipRoleType.BASE_DEFENSE):
                planet = universe.getPlanet(element.locationID)
                if not planet:
                    print >> sys.stderr, "Problem getting Planet for build loc %s" % element.locationID
                    continue
                sys_id = planet.systemID
                queued_defenses[sys_id] = queued_defenses.get(sys_id, 0) + element.blocksize*element.remaining
                defense_allocation += element.allocation
        print "Queued Defenses:", [(ppstring(PlanetUtilsAI.sys_name_ids([sys_id])), num)
                                   for sys_id, num in queued_defenses.items()]
        for sys_id, pids in state.get_empire_inhabited_planets_by_system().items():
            if foAI.foAIstate.systemStatus.get(sys_id, {}).get('fleetThreat', 1) > 0:
                continue  # don't build orbital shields if enemy fleet present
            if defense_allocation > max_defense_portion * total_pp:
                break
            sys_orbital_defenses[sys_id] = 0
            fleets_here = foAI.foAIstate.systemStatus.get(sys_id, {}).get('myfleets', [])
            for fid in fleets_here:
                if foAI.foAIstate.get_fleet_role(fid) == MissionType.ORBITAL_DEFENSE:
                    print "Found %d existing Orbital Defenses in %s :" % (foAI.foAIstate.fleetStatus.get(fid, {}).get(
                            'nships', 0), ppstring(PlanetUtilsAI.sys_name_ids([sys_id])))
                    sys_orbital_defenses[sys_id] += foAI.foAIstate.fleetStatus.get(fid, {}).get('nships', 0)
            for pid in pids:
                sys_orbital_defenses[sys_id] += queued_defenses.get(pid, 0)
            if sys_orbital_defenses[sys_id] < target_orbitals:
                num_needed = target_orbitals - sys_orbital_defenses[sys_id]
                for pid in pids:
                    best_design_id, col_design, build_choices = get_best_ship_info(
                            PriorityType.PRODUCTION_ORBITAL_DEFENSE, pid)
                    if not best_design_id:
                        print "no orbital defenses can be built at ", ppstring(PlanetUtilsAI.planet_name_ids([pid]))
                        continue
                    for i in xrange(num_needed):
                        foAI.foAIstate.production_queue_manager.enqueue_item(SHIP, best_design_id,
                                                                             pid, Priority.ship_orbital_defense)
                    break

    BuildingsAI.bld_cache.update()
    for building_name, building_manager in BuildingsAI.building_manager_map.iteritems():
        if empire.buildingTypeAvailable(building_name):
            building_manager().make_building_decision()
    
    find_best_designs_this_turn()
    ShipDesignAI.Cache.print_hulls_for_planets()
    ShipDesignAI.Cache.print_parts_for_planets()

    enqueued_yard = any(name in BuildingsAI.bld_cache.queued_buildings for name in ShipyardAI.shipyard_map)
    if not enqueued_yard:
        print "ENTERING SHIP BUILDING CYCLE"
        ShipyardAI.ShipyardManager.ai_priority = PriorityType.PRODUCTION_MILITARY
        ShipyardAI.ShipyardManager.ship_designer = ShipDesignAI.MilitaryShipDesigner
        best_candidate = None
        for shipyard, manager in ShipyardAI.shipyard_map.iteritems():
            this_manager = manager()
            if not empire.buildingTypeAvailable(shipyard) or not this_manager.prereqs_available():
                continue
            candidate = this_manager.get_candidate()
            if not candidate:
                continue
            if not best_candidate:
                best_candidate = candidate
                continue
            # first, check if we can afford the building.
            total_cost = candidate.get_total_pp_cost()
            current_count = 1 if candidate.improvement else len(
                    BuildingsAI.bld_cache.existing_buildings.get(candidate.name, []))
            allowance = 7 * BuildingsAI.bld_cache.total_production / current_count
            if total_cost > allowance:
                print "Total cost is %.1f but allowance is only %.1f! Do not build!" % (total_cost, allowance)
                continue
            if candidate.rating > best_candidate.rating:
                best_candidate = candidate
                print "This shipyard is currently the best shipyard!"
                continue
            if(candidate.rating == best_candidate.rating
               and candidate.get_total_pp_cost < best_candidate.get_total_pp_cost
               ):
                print "The shipyard %s is no improvement in rating but cheaper than %s" % (candidate.name,
                                                                                           best_candidate.name)
                best_candidate = candidate
                continue

        if best_candidate:
            if best_candidate.improvement:
                print "Shipyard %s is a global improvement! Set priority to high..." % best_candidate.name
                priority = Priority.building_high
            else:
                print "Shipyard %s is no global improvement... Set Priority to medium." % best_candidate.name
                priority = Priority.building_base
            missing_prereqs = best_candidate.get_missing_prereqs()
            missing_sys_prereqs = best_candidate.system_prereqs
            for building in missing_prereqs:
                print "Missing prerequisite %s (not system-wide) for %s" % (building, best_candidate.name)
                try:
                    foAI.foAIstate.production_queue_manager.enqueue_item(
                            BUILDING, building, best_candidate.pid, priority)
                except Exception:
                    # probably can't enqueue building...
                    print "Exception caught..."
                    continue
            for building in missing_sys_prereqs:
                print "Missing prerequisite %s (system-wide) for %s" % (building, best_candidate.name)
                for pid in PlanetUtilsAI.get_empire_planets_in_system(best_candidate.sys_id):
                    try:
                        res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, building, pid, priority,
                                                                                   print_enqueue_errors=False)
                    except Exception:
                        # Wrong location or building not available...
                        print "Exception caught..."
                        continue
                    if res:
                        break
            if not missing_prereqs or missing_sys_prereqs:
                if not best_candidate.shipyard_is_system_wide:
                    print "Trying to enqueue %s (not system-wide)" % best_candidate.name
                    foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, best_candidate.name,
                                                                         best_candidate.pid, priority)
                else:
                    # Some planet in the system has been found to be a valid location.
                    # Could, in principle, determine the pid before... But we are lazy and enqueue until it works :)
                    print "Trying to enqueue %s (system-wide)" % best_candidate.name
                    for pid in PlanetUtilsAI.get_empire_planets_in_system(best_candidate.sys_id):
                        if foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, best_candidate.name, pid,
                                                                                priority, print_enqueue_errors=False):
                            break

    colony_ship_map = {}
    for fid in FleetUtilsAI.get_empire_fleet_ids_by_role(MissionType.COLONISATION):
        fleet = universe.getFleet(fid)
        if not fleet:
            continue
        for shipID in fleet.shipIDs:
            ship = universe.getShip(shipID)
            if ship and (foAI.foAIstate.get_ship_role(ship.design.id) == ShipRoleType.CIVILIAN_COLONISATION):
                colony_ship_map.setdefault(ship.speciesName, []).append(1)

    building_name = "BLD_CONC_CAMP"
    verbose_camp = False
    building_type = fo.getBuildingType(building_name)
    for pid in AIstate.popCtrIDs:
        planet = universe.getPlanet(pid)
        if not planet:
            continue
        can_build_camp = (building_type.canBeProduced(empire.empireID, pid)
                          and empire.buildingTypeAvailable(building_name))
        t_pop = planet.currentMeterValue(fo.meterType.targetPopulation)
        c_pop = planet.currentMeterValue(fo.meterType.population)
        t_ind = planet.currentMeterValue(fo.meterType.targetIndustry)
        c_ind = planet.currentMeterValue(fo.meterType.industry)
        pop_disqualified = (c_pop <= 32) or (c_pop < 0.9*t_pop)
        this_spec = planet.speciesName
        safety_margin_met = (
            (this_spec in ColonisationAI.empire_colonizers and (
                len(state.get_empire_planets_with_species(this_spec)) + len(colony_ship_map.get(this_spec, [])) >= 2))
            or (c_pop >= 50))
        if pop_disqualified or not safety_margin_met:  # always check in case acquired planet with a ConcCamp on it
            if can_build_camp and verbose_camp:
                if pop_disqualified:
                    print "Conc Camp disqualified at %s due to low pop: current %.1f target: %.1f" % (planet.name,
                                                                                                      c_pop, t_pop)
                else:
                    print ("Conc Camp disqualified at %s due to safety margin; species %s,"
                           " colonizing planets %s, with %d colony ships"
                           % (planet.name, planet.speciesName,
                              state.get_empire_planets_with_species(planet.speciesName),
                              len(colony_ship_map.get(planet.speciesName, []))))
            for bldg in planet.buildingIDs:
                if universe.getBuilding(bldg).buildingTypeName == building_name:
                    res = fo.issueScrapOrder(bldg)
                    print "Tried scrapping %s at planet %s, got result %d" % (building_name, planet.name, res)
        elif foAI.foAIstate.character.may_build_building(building_name) and can_build_camp and (t_pop >= 36):
            if planet.focus == FocusType.FOCUS_GROWTH or "COMPUTRONIUM_SPECIAL" in planet.specials or pid == capital_id:
                continue
            queued_building_locs = [element.locationID for element in production_queue if element.name == building_name]
            if c_pop < 0.95 * t_pop:
                if verbose_camp:
                    print "Conc Camp disqualified at %s due to pop: current %.1f target: %.1f" % (planet.name,
                                                                                                  c_pop, t_pop)
            else:
                if pid not in queued_building_locs:
                    if planet.focus in [FocusType.FOCUS_INDUSTRY]:
                        if c_ind >= t_ind + c_pop:
                            continue
                    else:
                        old_focus = planet.focus
                        fo.issueChangeFocusOrder(pid, FocusType.FOCUS_INDUSTRY)
                        universe.updateMeterEstimates([pid])
                        t_ind = planet.currentMeterValue(fo.meterType.targetIndustry)
                        if c_ind >= t_ind + c_pop:
                            fo.issueChangeFocusOrder(pid, old_focus)
                            universe.updateMeterEstimates([pid])
                            continue
                    res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, building_name, pid,
                                                                               Priority.building_base)
                    if res:
                        queued_building_locs.append(pid)

    building_name = "BLD_SCANNING_FACILITY"
    if empire.buildingTypeAvailable(building_name):
        queued_locs = [element.locationID for element in production_queue if (element.name == building_name)]
        scanner_locs = {}
        for pid in list(AIstate.popCtrIDs) + list(AIstate.outpostIDs):
            planet = universe.getPlanet(pid)
            if planet:
                if (pid in queued_locs) or (building_name in [bld.buildingTypeName for bld in map(universe.getBuilding,
                                                                                                  planet.buildingIDs)]):
                    scanner_locs[planet.systemID] = True
        max_scanner_builds = max(1, int(empire.productionPoints / 30))
        for sys_id in AIstate.colonizedSystems:
            if len(queued_locs) >= max_scanner_builds:
                break
            if sys_id in scanner_locs:
                continue
            need_scanner = False
            for nSys in dict_from_map(universe.getSystemNeighborsMap(sys_id, empire.empireID)):
                if universe.getVisibility(nSys, empire.empireID) < fo.visibility.partial:
                    need_scanner = True
                    break
            if not need_scanner:
                continue
            build_locs = []
            for pid in AIstate.colonizedSystems[sys_id]:
                planet = universe.getPlanet(pid)
                if not planet:
                    continue
                build_locs.append((planet.currentMeterValue(fo.meterType.maxTroops), pid))
            if not build_locs:
                continue
            for troops, loc in sorted(build_locs):
                planet = universe.getPlanet(loc)
                res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, building_name, loc,
                                                                           Priority.building_low)
                if res:
                    queued_locs.append(planet.systemID)
                    break

    building_name = "BLD_XENORESURRECTION_LAB"
    queued_xeno_lab_locs = [element.locationID for element in production_queue if element.name == building_name]
    for pid in list(AIstate.popCtrIDs)+list(AIstate.outpostIDs):
        if pid in queued_xeno_lab_locs or not empire.canBuild(fo.buildType.building, building_name, pid):
            continue
        foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, building_name, pid, Priority.building_high)

    queued_clny_bld_locs = [element.locationID for element in production_queue if element.name.startswith('BLD_COL_')]
    colony_bldg_entries = ([entry for entry in foAI.foAIstate.colonisablePlanetIDs.items() if entry[1][0] > 60 and
                           entry[0] not in queued_clny_bld_locs and entry[0] in ColonisationAI.empire_outpost_ids]
                           [:PriorityAI.allottedColonyTargets+2])
    for entry in colony_bldg_entries:
        pid = entry[0]
        building_name = "BLD_COL_" + entry[1][1][3:]
        building_type = fo.getBuildingType(building_name)
        if not (building_type and building_type.canBeEnqueued(empire.empireID, pid)):
            continue
        res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, building_name, pid,
                                                                   Priority.building_high)
        if res:
            break

    building_name = "BLD_EVACUATION"
    for pid in AIstate.popCtrIDs:
        planet = universe.getPlanet(pid)
        if not planet:
            continue
        for bldg in planet.buildingIDs:
            if universe.getBuilding(bldg).buildingTypeName == building_name:
                res = fo.issueScrapOrder(bldg)
                print "Tried scrapping %s at planet %s, got result %d" % (building_name, planet.name, res)

    total_pp_spent = fo.getEmpire().productionQueue.totalSpent
    print "  Total Production Points Spent: %s" % total_pp_spent

    wasted_pp = max(0, total_pp - total_pp_spent)
    print "  Wasted Production Points: %s" % wasted_pp  # TODO: add resource group analysis
    avail_pp = total_pp - total_pp_spent - 0.0001

    print
    if False:
        print "Possible ship designs to build:"
        if homeworld:
            for ship_design_id in empire.availableShipDesigns:
                design = fo.getShipDesign(ship_design_id)
                print "    %s cost: %s  time: %s" % (design.name,
                                                     design.productionCost(empire.empireID, homeworld.id),
                                                     design.productionTime(empire.empireID, homeworld.id))
    print
    production_queue = empire.productionQueue
    queued_colony_ships = {}
    queued_outpost_ships = 0
    queued_troop_ships = 0

    # TODO: blocked items might not need dequeuing, but rather for supply lines to be un-blockaded
    fo.updateProductionQueue()
    can_prioritize_troops = False
    for queue_index in range(len(production_queue)):
        element = production_queue[queue_index]
        block_str = "%d x " % element.blocksize
        print "    %s%s  requiring %s  more turns; alloc: %.2f PP with cum. progress of %.1f  being built at %s" % (
            block_str, element.name, element.turnsLeft, element.allocation,
            element.progress, universe.getObject(element.locationID).name)
        if element.turnsLeft == -1:
            if element.locationID not in AIstate.popCtrIDs + AIstate.outpostIDs:
                print ("element %s will never be completed as stands and location %d no longer owned;"
                       " could consider deleting from queue" % (element.name, element.locationID))  # TODO:
            else:
                print ("element %s is projected to never be completed as currently stands,"
                       " but will remain on queue " % element.name)
        elif element.buildType == EmpireProductionTypes.BT_SHIP:
            this_role = foAI.foAIstate.get_ship_role(element.designID)
            if this_role == ShipRoleType.CIVILIAN_COLONISATION:
                this_spec = universe.getPlanet(element.locationID).speciesName
                queued_colony_ships[this_spec] = queued_colony_ships.get(
                        this_spec, 0) + element.remaining * element.blocksize
            elif this_role == ShipRoleType.CIVILIAN_OUTPOST:
                queued_outpost_ships += element.remaining * element.blocksize
            elif this_role == ShipRoleType.BASE_OUTPOST:
                queued_outpost_ships += element.remaining * element.blocksize
            elif this_role == ShipRoleType.MILITARY_INVASION:
                queued_troop_ships += element.remaining * element.blocksize
            elif (this_role == ShipRoleType.CIVILIAN_EXPLORATION) and (queue_index <= 1):
                if len(AIstate.opponentPlanetIDs) > 0:
                    can_prioritize_troops = True
    if queued_colony_ships:
        print "\nFound colony ships in build queue: %s" % queued_colony_ships
    if queued_outpost_ships:
        print "\nFound outpost ships and bases in build queue: %s" % queued_outpost_ships

    all_military_fleet_ids = FleetUtilsAI.get_empire_fleet_ids_by_role(MissionType.MILITARY)
    total_military_ships = sum([foAI.foAIstate.fleetStatus.get(fid, {}).get('nships', 0)
                                for fid in all_military_fleet_ids])
    all_troop_fleet_ids = FleetUtilsAI.get_empire_fleet_ids_by_role(MissionType.INVASION)
    total_troop_ships = sum([foAI.foAIstate.fleetStatus.get(fid, {}).get('nships', 0)
                             for fid in all_troop_fleet_ids])
    avail_troop_fleet_ids = list(FleetUtilsAI.extract_fleet_ids_without_mission_types(all_troop_fleet_ids))
    total_available_troops = sum([foAI.foAIstate.fleetStatus.get(fid, {}).get('nships', 0)
                                  for fid in avail_troop_fleet_ids])
    print ("Trooper Status turn %d: %d total, with %d unassigned."
           " %d queued, compared to %d total Military Attack Ships"
           % (current_turn, total_troop_ships, total_available_troops, queued_troop_ships, total_military_ships))
    if (
        capital_id is not None and
        (current_turn >= 40 or can_prioritize_troops) and
        foAI.foAIstate.systemStatus.get(capital_system_id, {}).get('fleetThreat', 0) == 0 and
        foAI.foAIstate.systemStatus.get(capital_system_id, {}).get('neighborThreat', 0) == 0
    ):
        best_design_id, best_design, build_choices = get_best_ship_info(PriorityType.PRODUCTION_INVASION)
        if build_choices is not None and len(build_choices) > 0:
            loc = random.choice(build_choices)
            prod_time = best_design.productionTime(empire.empireID, loc)
            prod_cost = best_design.productionCost(empire.empireID, loc)
            troopers_needed = max(0, int(min(0.99 + (current_turn/20.0 - total_available_troops)/max(2, prod_time - 1),
                                             total_military_ships/3 - total_troop_ships)))
            ship_number = troopers_needed
            per_turn_cost = (float(prod_cost) / prod_time)
            if (troopers_needed > 0 and total_pp > 3*per_turn_cost*queued_troop_ships
                    and foAI.foAIstate.character.may_produce_troops()):
                retval = 0
                for i in xrange(ship_number):
                    retval = foAI.foAIstate.production_queue_manager.enqueue_item(SHIP, best_design_id, loc,
                                                                                  Priority.ship_troops)
                if retval:
                    avail_pp -= ship_number * per_turn_cost
                    fo.updateProductionQueue()
        print

    print
    # get the highest production priorities
    production_priorities = {}
    for priority_type in get_priority_production_types():
        production_priorities[priority_type] = int(max(0, (foAI.foAIstate.get_priority(priority_type)) ** 0.5))

    sorted_priorities = production_priorities.items()
    sorted_priorities.sort(lambda x, y: cmp(x[1], y[1]), reverse=True)

    top_score = -1

    # counting existing colony and outpost fleets each as one ship
    num_colony_fleets = len(FleetUtilsAI.get_empire_fleet_ids_by_role(MissionType.COLONISATION))
    total_colony_fleets = sum(queued_colony_ships.values()) + num_colony_fleets
    num_outpost_fleets = len(FleetUtilsAI.get_empire_fleet_ids_by_role(MissionType.OUTPOST))
    total_outpost_fleets = queued_outpost_ships + num_outpost_fleets

    max_colony_fleets = PriorityAI.allottedColonyTargets
    max_outpost_fleets = max_colony_fleets

    _, _, colony_build_choices = get_best_ship_info(PriorityType.PRODUCTION_COLONISATION)
    military_emergency = PriorityAI.unmetThreat > (2.0 * MilitaryAI.get_tot_mil_rating())

    print "Production Queue Priorities:"
    filtered_priorities = {}
    for priority_id, score in sorted_priorities:
        if military_emergency:
            if priority_id == PriorityType.PRODUCTION_EXPLORATION:
                score /= 10.0
            elif priority_id != PriorityType.PRODUCTION_MILITARY:
                score /= 2.0
        if top_score < score:
            top_score = score  # don't really need top_score nor sorting with current handling
        print " Score: %4d -- %s " % (score, priority_id)
        if priority_id != PriorityType.PRODUCTION_BUILDINGS:
            if ((priority_id == PriorityType.PRODUCTION_COLONISATION)
                    and (total_colony_fleets < max_colony_fleets)
                    and (colony_build_choices is not None)
                    and len(colony_build_choices) > 0):
                filtered_priorities[priority_id] = score
            elif (priority_id == PriorityType.PRODUCTION_OUTPOST) and (total_outpost_fleets < max_outpost_fleets):
                filtered_priorities[priority_id] = score
            elif priority_id not in [PriorityType.PRODUCTION_OUTPOST, PriorityType.PRODUCTION_COLONISATION]:
                filtered_priorities[priority_id] = score
    if filtered_priorities == {}:
        print "No non-building-production priorities with nonzero score, setting to default: Military"
        filtered_priorities[PriorityType.PRODUCTION_MILITARY] = 1
    if top_score <= 100:
        scaling_power = 1.0
    else:
        scaling_power = math.log(100) / math.log(top_score)
    for pty in filtered_priorities:
        filtered_priorities[pty] **= scaling_power

    # keys are sets of ints; data is doubles
    available_pp = dict([(tuple(el.key()), el.data()) for el in empire.planetsWithAvailablePP])
    allocated_pp = dict([(tuple(el.key()), el.data()) for el in empire.planetsWithAllocatedPP])
    planets_with_wasted_pp = set([tuple(pidset) for pidset in empire.planetsWithWastedPP])
    print "avail_pp ( <systems> : pp ):"
    for planet_set in available_pp:
        print "\t%s\t%.2f" % (ppstring(PlanetUtilsAI.sys_name_ids(set(PlanetUtilsAI.get_systems(planet_set)))),
                              available_pp[planet_set])
    print "\nallocated_pp ( <systems> : pp ):"
    for planet_set in allocated_pp:
        print "\t%s\t%.2f" % (ppstring(PlanetUtilsAI.sys_name_ids(set(PlanetUtilsAI.get_systems(planet_set)))),
                              allocated_pp[planet_set])

    print "\n\nBuilding Ships in system groups with remaining PP:"
    for planet_set in planets_with_wasted_pp:
        total_pp = available_pp.get(planet_set, 0)
        avail_pp = total_pp - allocated_pp.get(planet_set, 0)
        if avail_pp <= 0.01:
            continue
        print "%.2f PP remaining in system group: %s" % (avail_pp, ppstring(
                PlanetUtilsAI.sys_name_ids(set(PlanetUtilsAI.get_systems(planet_set)))))
        print "\t owned planets in this group are:"
        print "\t %s" % (ppstring(PlanetUtilsAI.planet_name_ids(planet_set)))
        best_design_id, best_design, build_choices = get_best_ship_info(PriorityType.PRODUCTION_COLONISATION,
                                                                        list(planet_set))
        species_map = {}
        for loc in (build_choices or []):
            this_spec = universe.getPlanet(loc).speciesName
            species_map.setdefault(this_spec, []).append(loc)
        colony_build_choices = []
        for pid, (score, this_spec) in foAI.foAIstate.colonisablePlanetIDs.items():
            colony_build_choices.extend(int(math.ceil(score))*[pid2 for pid2 in species_map.get(this_spec, [])
                                                               if pid2 in planet_set])

        local_priorities = {}
        local_priorities.update(filtered_priorities)
        best_ships = {}
        mil_build_choices = get_best_ship_ratings(planet_set)
        for priority in list(local_priorities):
            if priority == PriorityType.PRODUCTION_MILITARY:
                if not mil_build_choices:
                    del local_priorities[priority]
                    continue
                _, pid, best_design_id, best_design = mil_build_choices[0]
                build_choices = [pid]
                # score = ColonisationAI.pilotRatings.get(pid, 0)
                # if bestScore < ColonisationAI.curMidPilotRating:
            else:
                best_design_id, best_design, build_choices = get_best_ship_info(priority, list(planet_set))
            if best_design is None:
                del local_priorities[priority]  # must be missing a shipyard -- TODO build a shipyard if necessary
                continue
            best_ships[priority] = [best_design_id, best_design, build_choices]
            print "best_ships[%s] = %s \t locs are %s from %s" % (priority, best_design.name, build_choices, planet_set)

        if len(local_priorities) == 0:
            print "Alert!! need shipyards in systemSet ", ppstring(PlanetUtilsAI.sys_name_ids(
                    set(PlanetUtilsAI.get_systems(sorted(planet_set)))))
        priority_choices = []
        for priority in local_priorities:
            priority_choices.extend(int(local_priorities[priority]) * [priority])

        loop_count = 0
        while (avail_pp > 0) and (loop_count < max(100, current_turn)) and (priority_choices != []):
            # make sure don't get stuck in some nonbreaking loop like if all shipyards captured
            loop_count += 1
            print "Beginning build enqueue loop %d; %.1f PP available" % (loop_count, avail_pp)
            this_priority = random.choice(priority_choices)
            print "selected priority: ", this_priority
            making_colony_ship = False
            making_outpost_ship = False
            if this_priority == PriorityType.PRODUCTION_COLONISATION:
                if total_colony_fleets >= max_colony_fleets:
                    print "Already sufficient colony ships in queue, trying next priority choice"
                    print
                    for i in range(len(priority_choices) - 1, -1, -1):
                        if priority_choices[i] == PriorityType.PRODUCTION_COLONISATION:
                            del priority_choices[i]
                    continue
                elif colony_build_choices is None or len(colony_build_choices) == 0:
                    for i in range(len(priority_choices) - 1, -1, -1):
                        if priority_choices[i] == PriorityType.PRODUCTION_COLONISATION:
                            del priority_choices[i]
                    continue
                else:
                    making_colony_ship = True
            if this_priority == PriorityType.PRODUCTION_OUTPOST:
                if total_outpost_fleets >= max_outpost_fleets:
                    print "Already sufficient outpost ships in queue, trying next priority choice"
                    print
                    for i in range(len(priority_choices) - 1, -1, -1):
                        if priority_choices[i] == PriorityType.PRODUCTION_OUTPOST:
                            del priority_choices[i]
                    continue
                else:
                    making_outpost_ship = True
            best_design_id, best_design, build_choices = best_ships[this_priority]
            if making_colony_ship:
                loc = random.choice(colony_build_choices)
                best_design_id, best_design, build_choices = get_best_ship_info(
                        PriorityType.PRODUCTION_COLONISATION, loc)
            elif this_priority == PriorityType.PRODUCTION_MILITARY:
                selector = random.random()
                choice = mil_build_choices[0]  # mil_build_choices can't be empty due to earlier check
                for choice in mil_build_choices:
                    if choice[0] >= selector:
                        break
                loc, best_design_id, best_design = choice[1:4]
                if best_design is None:
                    print >> sys.stderr, ("problem with mil_build_choices; with selector (%s) chose loc (%s),"
                                          " best_design_id (%s), best_design (None) from mil_build_choices: %s"
                                          % (selector, loc, best_design_id, mil_build_choices))
                    continue
            else:
                loc = random.choice(build_choices)

            ship_number = 1
            per_turn_cost = (float(best_design.productionCost(empire.empireID, loc)) / best_design.productionTime(
                    empire.empireID, loc))
            if this_priority == PriorityType.PRODUCTION_MILITARY:
                this_rating = ColonisationAI.pilot_ratings.get(loc, 0)
                rating_ratio = float(this_rating) / state.best_pilot_rating
                if rating_ratio < 0.1:
                    loc_planet = universe.getPlanet(loc)
                    if loc_planet:
                        pname = loc_planet.name
                        this_rating = ColonisationAI.rate_planetary_piloting(loc)
                        rating_ratio = float(this_rating) / state.best_pilot_rating
                        qualifier = ["", "suboptimal"][rating_ratio < 1.0]
                        print ("Building mil ship at loc %d (%s) with %s pilot Rating: %.1f;"
                               " ratio to empire best is %.1f"
                               % (loc, pname, qualifier, this_rating, rating_ratio))
                while total_pp > 40 * per_turn_cost:
                    ship_number *= 2
                    per_turn_cost *= 2
            retval = foAI.foAIstate.production_queue_manager.enqueue_item(SHIP, best_design_id, loc,
                                                                          Priority.ship_mil)
            if retval != 0:
                print "adding %d new ship(s) at location %s to production queue: %s; per turn production cost %.1f" % (
                    ship_number, ppstring(PlanetUtilsAI.planet_name_ids([loc])), best_design.name, per_turn_cost)
                print
                if ship_number > 1:
                    fo.issueChangeProductionQuantityOrder(production_queue.size - 1, 1, ship_number)
                avail_pp -= per_turn_cost
                if making_colony_ship:
                    total_colony_fleets += ship_number
                    continue
                if making_outpost_ship:
                    total_outpost_fleets += ship_number
                    continue
                if total_pp > 10 * per_turn_cost:
                    leading_block_pp = 0
                    for elem in [production_queue[elemi] for elemi in range(0, min(4, production_queue.size))]:
                        cost, time = empire.productionCostAndTime(elem)
                        leading_block_pp += elem.blocksize * cost / time
        print
    fo.updateProductionQueue()
    _print_production_queue(after_turn=True)


def _print_production_queue(after_turn=False):
    """Print production queue content with relevant info in table format."""
    universe = fo.getUniverse()
    s = "after" if after_turn else "before"
    title = "Production Queue Turn %d %s ProductionAI calls" % (fo.currentTurn(), s)
    prod_queue_table = Table(
        [Text('Object'), Text('Location'), Text('Quantity'),
         Text('Progress'), Text('Allocated PP'), Text('Turns left')],
        table_name=title
    )
    for element in fo.getEmpire().productionQueue:
        if element.buildType == EmpireProductionTypes.BT_SHIP:
            item = fo.getShipDesign(element.designID)
        elif element.buildType == EmpireProductionTypes.BT_BUILDING:
            item = fo.getBuildingType(element.name)
        else:
            continue
        cost = item.productionCost(fo.empireID(), element.locationID)

        prod_queue_table.add_row([
            element.name,
            universe.getPlanet(element.locationID),
            "%dx %d" % (element.remaining, element.blocksize),
            "%.1f / %.1f" % (element.progress, cost),
            "%.1f" % element.allocation,
            "%d" % element.turnsLeft,
        ])
    prod_queue_table.print_table()


def find_automatic_historic_analyzer_candidates():
    """
    Find possible locations for the BLD_AUTO_HISTORY_ANALYSER building and return a subset of chosen building locations.

    :return: Random possible locations up to max queueable amount. Empty if no location found or can't queue another one
    :rtype: list
    """
    empire = fo.getEmpire()
    universe = fo.getUniverse()
    total_pp = empire.productionPoints
    history_analyser = "BLD_AUTO_HISTORY_ANALYSER"
    culture_archives = "BLD_CULTURE_ARCHIVES"

    conditions = {
        # aggression: (min_pp, min_turn, min_pp_to_queue_another_one)
        fo.aggression.beginner: (100, 100, ARB_LARGE_NUMBER),
        fo.aggression.turtle: (75, 75, ARB_LARGE_NUMBER),
        fo.aggression.cautious: (60, 60, ARB_LARGE_NUMBER),
        fo.aggression.typical: (30, 50, ARB_LARGE_NUMBER),
        fo.aggression.aggressive: (25, 50, ARB_LARGE_NUMBER),
        fo.aggression.maniacal: (25, 50, 100)
    }

    min_pp, turn_trigger, min_pp_per_additional = conditions.get(foAI.foAIstate.character.get_trait(Aggression).key,
                                                                 (ARB_LARGE_NUMBER, ARB_LARGE_NUMBER, ARB_LARGE_NUMBER))
    # If we can colonize good planets instead, do not build this.
    num_colony_targets = 0
    for pid in ColonisationAI.all_colony_opportunities:
        try:
            best_species_score = ColonisationAI.all_colony_opportunities[pid][0][0]
        except IndexError:
            continue
        if best_species_score > 500:
            num_colony_targets += 1

    num_covered = get_number_of_existing_outpost_and_colony_ships() + get_number_of_queued_outpost_and_colony_ships()
    remaining_targets = num_colony_targets - num_covered
    min_pp *= remaining_targets

    max_enqueued = 1 if total_pp > min_pp or fo.currentTurn() > turn_trigger else 0
    max_enqueued += int(total_pp / min_pp_per_additional)

    if max_enqueued <= 0:
        return []

    # find possible locations
    possible_locations = set()
    for pid in list(AIstate.popCtrIDs) + list(AIstate.outpostIDs):
        planet = universe.getPlanet(pid)
        if not planet or planet.currentMeterValue(fo.meterType.targetPopulation) < 1:
            continue
        buildings_here = [bld.buildingTypeName for bld in map(universe.getBuilding, planet.buildingIDs)]
        if planet and culture_archives in buildings_here and history_analyser not in buildings_here:
            possible_locations.add(pid)

    # check existing queued buildings and remove from possible locations
    queued_locs = {e.locationID for e in empire.productionQueue if e.buildType == EmpireProductionTypes.BT_BUILDING and
                   e.name == history_analyser}

    possible_locations -= queued_locs
    chosen_locations = []
    for i in range(min(max_enqueued, len(possible_locations))):
        chosen_locations.append(possible_locations.pop())
    return chosen_locations


def get_number_of_queued_outpost_and_colony_ships():
    """Get the total number of queued outpost/colony ships/bases.

    :rtype: int
    """
    num_ships = 0
    for element in fo.getEmpire().productionQueue:
        if element.turnsLeft >= 0 and element.buildType == EmpireProductionTypes.BT_SHIP:
            if foAI.foAIstate.get_ship_role(element.designID) in (ShipRoleType.CIVILIAN_OUTPOST,
                                                                  ShipRoleType.BASE_OUTPOST,
                                                                  ShipRoleType.BASE_COLONISATION,
                                                                  ShipRoleType.CIVILIAN_COLONISATION):
                num_ships += element.blocksize
    return num_ships


def get_number_of_existing_outpost_and_colony_ships():
    """Get the total number of existing outpost/colony ships/bases.

    :rtype: int
    """
    num_colony_fleets = len(FleetUtilsAI.get_empire_fleet_ids_by_role(MissionType.COLONISATION))
    num_outpost_fleets = len(FleetUtilsAI.get_empire_fleet_ids_by_role(MissionType.OUTPOST))
    return num_outpost_fleets + num_colony_fleets
