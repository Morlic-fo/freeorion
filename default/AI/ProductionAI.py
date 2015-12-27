import freeOrionAIInterface as fo  # pylint: disable=import-error
import AIDependencies
import AIstate
import ColonisationAI
import EnumsAI
import FleetUtilsAI
import FreeOrionAI as foAI
import MilitaryAI
import PlanetUtilsAI
import PriorityAI
import ShipDesignAI
from freeorion_tools import dict_from_map, ppstring, chat_human, tech_is_complete, print_error
# python standard library imports
import bisect  # used for ordered list implementation
import cProfile, pstats, StringIO  # profiling of ShipDesignAI code calls
import time
import math
import random
import sys

BUILDING = EnumsAI.AIEmpireProductionTypes.BT_BUILDING
SHIP = EnumsAI.AIEmpireProductionTypes.BT_SHIP
# lower number means higher priority
PRIORITY_EMERGENCY_FACTOR = 1e-9
PRIORITY_DEFAULT = 100
PRIORITY_BUILDING_LOW = 1000
PRIORITY_BUILDING_BASE = 100
PRIORITY_BUILDING_HIGH = 1
PRIORITY_SHIP_SCOUT = 100
PRIORITY_ORBITAL_DEFENSE = 90
PRIORITY_SHIP_MIL = 80
PRIORITY_SHIP_OUTPOST = 70
PRIORITY_SHIP_COLO = 60
PRIORITY_SHIP_TROOPS = 50
PRIORITY_SHIP_ORBITAL_OUTPOST = 40
PRIORITY_SHIP_ORBITAL_COLO = 30
PRIORITY_SHIP_ORBITAL_TROOPS = 20
PRIORITY_INVALID = 99999  # large number to ensure to be at the end of the list

best_military_design_rating_cache = {}  # indexed by turn, values are rating of the military design of the turn
design_cost_cache = {0: {
    (-1, -1): 0}}  # outer dict indexed by cur_turn (currently only one turn kept); inner dict indexed by (design_id, pid)
shipTypeMap = {EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_EXPLORATION: EnumsAI.AIShipDesignTypes.explorationShip,
               EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_OUTPOST: EnumsAI.AIShipDesignTypes.outpostShip,
               EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_ORBITAL_OUTPOST: EnumsAI.AIShipDesignTypes.outpostBase,
               EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_COLONISATION: EnumsAI.AIShipDesignTypes.colonyShip,
               EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_ORBITAL_COLONISATION: EnumsAI.AIShipDesignTypes.colonyBase,
               EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_INVASION: EnumsAI.AIShipDesignTypes.troopShip,
               EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_MILITARY: EnumsAI.AIShipDesignTypes.attackShip,
               EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_ORBITAL_DEFENSE: EnumsAI.AIShipDesignTypes.defenseBase,
               EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_ORBITAL_INVASION: EnumsAI.AIShipDesignTypes.troopBase,
               }

design_cache = {}  # dict of tuples (rating,pid,designID,cost) sorted by rating and indexed by priority type

_CHAT_DEBUG = False


def find_best_designs_this_turn():
    """Calculate the best designs for each ship class available at this turn."""
    pr = cProfile.Profile()
    pr.enable()
    start = time.clock()
    ShipDesignAI.Cache.update_for_new_turn()
    design_cache.clear()
    design_cache[EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_MILITARY] = ShipDesignAI.MilitaryShipDesigner().optimize_design()
    design_cache[EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_ORBITAL_INVASION] = ShipDesignAI.OrbitalTroopShipDesigner().optimize_design()
    design_cache[EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_INVASION] = ShipDesignAI.StandardTroopShipDesigner().optimize_design()
    design_cache[EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_COLONISATION] = ShipDesignAI.StandardColonisationShipDesigner().optimize_design()
    design_cache[EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_ORBITAL_COLONISATION] = ShipDesignAI.OrbitalColonisationShipDesigner().optimize_design()
    design_cache[EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_OUTPOST] = ShipDesignAI.StandardOutpostShipDesigner().optimize_design()
    design_cache[EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_ORBITAL_OUTPOST] = ShipDesignAI.OrbitalOutpostShipDesigner().optimize_design()
    design_cache[EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_ORBITAL_DEFENSE] = ShipDesignAI.OrbitalDefenseShipDesigner().optimize_design()
    design_cache[EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_EXPLORATION] = ShipDesignAI.ScoutShipDesigner().optimize_design()
    ShipDesignAI.KrillSpawnerShipDesigner().optimize_design()  # just designing it, building+mission not supported yet
    end = time.clock()
    print "DEBUG INFORMATION: The design evaluations took %f s" % (end - start)
    print "-----"
    pr.disable()
    s = StringIO.StringIO()
    sortby = 'cumulative'
    ps = pstats.Stats(pr, stream=s).sort_stats(sortby)
    ps.print_stats()
    print s.getvalue()
    print "-----"
    if fo.currentTurn() % 10 == 0:
        ShipDesignAI.Cache.print_best_designs()


def get_design_cost(design, pid):  # TODO: Use new framework
    """Find and return the design_cost of the specified design on the specified planet.

    :param design:
    :type design: fo.shipDesign
    :param pid: planet id
    :type pid: int
    :return: cost of the design
    """
    cur_turn = fo.currentTurn()
    if cur_turn in design_cost_cache:
        cost_cache = design_cost_cache[cur_turn]
    else:
        design_cost_cache.clear()
        cost_cache = {}
        design_cost_cache[cur_turn] = cost_cache
    loc_invariant = design.costTimeLocationInvariant
    if loc_invariant:
        loc = -1
    else:
        loc = pid
    return cost_cache.setdefault((design.id, loc), design.productionCost(fo.empireID(), pid))


def cur_best_military_design_rating():
    """Find and return the default combat rating of our best military design.

    :return: float: rating of the best military design
    """
    current_turn = fo.currentTurn()
    if current_turn in best_military_design_rating_cache:
        return best_military_design_rating_cache[current_turn]
    priority = EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_MILITARY
    if priority in design_cache:
        if design_cache[priority] and design_cache[priority][0]:
            rating, pid, design_id, cost = design_cache[priority][0]
            pilots = fo.getUniverse().getPlanet(pid).speciesName
            ship_id = -1  # no existing ship
            design_rating = foAI.foAIstate.rate_psuedo_fleet(ship_info=[(ship_id, design_id, pilots)])['overall']
            best_military_design_rating_cache[current_turn] = design_rating
            return max(design_rating, 0.001)
        else:
            return 0.001
    else:
        return 0.001


def get_best_ship_info(priority, loc=None):
    """Get best available ship design and list of build locations.

    :param priority: Type of ship to be built
    :type priority: EnumsAI.AIPriorityType
    :param loc: Locations to be queried.
    :type loc: list | int | None
    :return: (designID, design, [build_loc_list])
    :rtype: tuple
    """
    if loc is None:
        planet_ids = AIstate.popCtrIDs
    elif isinstance(loc, list):
        planet_ids = set(loc).intersection(AIstate.popCtrIDs)
    elif isinstance(loc, int) and loc in AIstate.popCtrIDs:
        planet_ids = [loc]
    else:  # problem
        return None, None, None
    if priority in design_cache:
        best_designs = design_cache[priority]
        if not best_designs:
            return None, None, None
        top_rating = top_id = None
        for design_stats in best_designs:
            top_rating, pid, top_id, cost = design_stats
            if pid in planet_ids:
                break
        valid_locs = [item[1] for item in best_designs if item[0] == top_rating and item[2] == top_id]
        return top_id, fo.getShipDesign(top_id), valid_locs
    else:
        return None, None, None  # must be missing a Shipyard or other orbital (or missing tech)


def get_best_ship_ratings(loc=None):
    """Get a list of candidates of location and design for military ships.

    :param loc: Locations (planet ids) to be queried
    :type loc: list | int | None
    :return: [partition, pid, designID, design]
    :rtype: list
    """
    # Since we haven't yet implemented a way to target military ship construction at/near particular locations
    # where they are most in need, and also because our rating system is presumably useful-but-not-perfect, we want to
    # distribute the construction across the Resource Group and across similarly rated designs, preferentially choosing
    # the best rated design/loc combo, but if there are multiple design/loc combos with the same or similar ratings then
    # we want some chance of choosing  those alternate designs/locations.
    #
    # The approach to this taken below is to treat the ratings akin to an energy to be used in a statistical mechanics
    # type partition function. 'tally' will compute the normalization constant.
    # So first go through and calculate the tally as well as convert each individual contribution to
    # the running total up to that point, to facilitate later sampling.  Then those running totals are
    # renormalized by the final tally, so that a later random number selector in the range [0,1) can be
    # used to select the chosen design/loc.
    priority = EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_MILITARY
    if loc is None:
        planet_ids = ColonisationAI.empire_shipyards
    elif isinstance(loc, list):
        planet_ids = set(loc).intersection(ColonisationAI.empire_shipyards)
    elif isinstance(loc, int):
        if loc in ColonisationAI.empire_shipyards:
            planet_ids = [loc]
        else:
            return []
    else:  # problem
        return []

    if priority in design_cache:  # use new framework
        build_choices = design_cache[priority]
        loc_choices = [[item[0], item[1], item[2], fo.getShipDesign(item[2])]
                       for item in build_choices if item[1] in planet_ids]
        if not loc_choices:
            return []
        best_rating = loc_choices[0][0]
        tally = 0
        ret_val = []
        for choice in loc_choices:
            if choice[0] < 0.7 * best_rating:
                break
            p = math.exp(10 * (choice[0] / best_rating - 1))
            tally += p
            ret_val.append([tally, choice[1], choice[2], choice[3]])
        for item in ret_val:
            item[0] /= tally
        return ret_val
    else:
        return []


class BuildingCache(object):
    """Caches stuff important to buildings..."""
    existing_buildings = None
    queued_buildings = None
    n_production_focus = None
    n_research_focus = None
    total_production = None

    def __init__(self):
        self.update()

    def update(self):
        """Update the cache."""
        self.existing_buildings = _get_all_existing_buildings()
        self.queued_buildings = foAI.foAIstate.production_queue_manager.get_all_queued_buildings()
        self.n_production_focus, self.n_research_focus = _count_empire_foci()
        self.total_production = fo.getEmpire().productionPoints

building_cache = BuildingCache()


class BuildingManager(object):
    """Manages the construction of buildings."""
    name = ""
    production_cost = 99999
    production_time = 99999
    priority = PRIORITY_BUILDING_BASE
    minimum_aggression = fo.aggression.beginner

    def __init__(self):
        self.building = fo.getBuildingType(self.name)
        if not self.building:
            print "Specified invalid building!"
        else:
            empire_id = fo.getEmpireID()
            capital_id = PlanetUtilsAI.get_capital()
            self.production_cost = self.building.productionCost(empire_id, capital_id)
            self.production_time = self.building.productionTime(empire_id, capital_id)

    def make_building_decision(self):
        """Make a decision if we want to build the building and if so, enqueue it.

         :return: True if we enqueued it, otherwise False
         :rtype: bool
        """
        if self._should_be_built():
            for loc in self._enqueue_locations():
                foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, self.name, loc, self.priority)

    def _suitable_locations(self):
        """Return a list of suitable locations for the building"""
        raise NotImplementedError

    def _enqueue_locations(self):
        """Return the enqueue locations for this building.

        :return: planet_ids where to enqueue the building.
        :rtype: list
        """
        raise NotImplementedError

    def _need_another_one(self):
        """Check if we need another one of this building.

        :return: True if we need another one, otherwise false
        :rtype: bool
        """
        raise NotImplementedError

    def _should_be_built(self):
        """Return if we want to build this building.

        :return: True if we want to build this somewhere
        :rtype: bool
        """
        if foAI.foAIstate.aggression < self.minimum_aggression:
            return False
        if not self._need_another_one():
            return False
        if not self._suitable_locations():
            return False
        # passed all tests
        return True


class EconomyBoostBuildingManager(BuildingManager):
    needs_production_focus = False
    needs_research_focus = False
    RP_TO_PP_CONVERSION_FACTOR = 2.0

    # overloaded functions
    def _suitable_locations(self):
        # default: any populated planet
        return list(AIstate.popCtrIDs)

    def _enqueue_locations(self):
        capital_id = PlanetUtilsAI.get_capital()
        if capital_id != -1:
            return [capital_id]
        locs = self._suitable_locations()
        if locs:
            return [locs[0]]
        else:
            return None

    def _need_another_one(self):
        # default: Build only once per empire
        if self.name in building_cache.existing_buildings:
            return False
        if self.name in building_cache.queued_buildings:
            return False
        return True

    def _should_be_built(self):
        cost_per_turn = float(self.production_cost)/self.production_time
        turns_till_payoff = self._estimated_time_to_payoff()
        if not BuildingManager._should_be_built(self):  # aggression level, need for another one, have locations...
            return False
        if self.production_cost > 10*building_cache.total_production:
            return False
        if turns_till_payoff < 10 and cost_per_turn < 2*building_cache.total_production:
            return True
        if turns_till_payoff < 20 and cost_per_turn < building_cache.total_production:
            return True
        if self._estimated_time_to_payoff() < 50 and cost_per_turn < .1*building_cache.total_production:
            return True
        return False

    # new function definitions
    def _production_per_pop(self):
        """Return production granted per population.

        :return: production per population
        :rtype: float
        """
        return 0.0

    def _flat_production_bonus(self):
        """Return flat production bonus granted by building.

        :return: flat production bonus granted by building
        :rtype: float
        """
        return 0.0

    def _research_per_pop(self):
        """Return research granted per population.

        :return:
        :rtype: float
        """
        return 0.0

    def _flat_research_bonus(self):
        """Return flat research bonus granted by building

        :return:
        :rtype: float
        """
        return 0.0

    def _total_research(self):
        if self.needs_research_focus:
            number_of_pop_ctrs = building_cache.n_research_focus
            relevant_population = ColonisationAI.empire_status['researchers']
        else:
            number_of_pop_ctrs = len(AIstate.popCtrIDs)
            relevant_population = fo.getEmpire().population()
        return number_of_pop_ctrs*self._flat_research_bonus() + relevant_population*self._research_per_pop()

    def _total_production(self):
        if self.needs_production_focus:
            number_of_pop_ctrs = building_cache.n_production_focus
            relevant_population = ColonisationAI.empire_status['industrialists']
        else:
            number_of_pop_ctrs = len(AIstate.popCtrIDs)
            relevant_population = fo.getEmpire().population()
        return number_of_pop_ctrs*self._flat_production_bonus() + relevant_population*self._production_per_pop()

    def _estimated_time_to_payoff(self):
        """Returns an estimation of turns until the building returns payed for itself.

        :return: number of turns until payoff
        :rtype: float
        """
        # TODO: Consider the effects of changing focus
        total_economy_points = self._total_production() + self._total_research()*self.RP_TO_PP_CONVERSION_FACTOR
        return float(self.production_cost) / max(total_economy_points, 1e-12)


class IndustrialCenterManager(EconomyBoostBuildingManager):
    """Handles building decisions for the industrial center."""
    name = "BLD_INDUSTRY_CENTER"
    needs_production_focus = True
    priority = PRIORITY_BUILDING_HIGH

    def _production_per_pop(self):
        if tech_is_complete("PRO_INDUSTRY_CENTER_III"):
            return 3.0 * AIDependencies.INDUSTRY_PER_POP
        elif tech_is_complete("PRO_INDUSTRY_CENTER_II"):
            return 2.0 * AIDependencies.INDUSTRY_PER_POP
        elif tech_is_complete("PRO_INDUSTRY_CENTER_I"):
            return 1.0 * AIDependencies.INDUSTRY_PER_POP
        else:
            return 0.0


def generate_production_orders():
    """Generate production orders."""
    # first check ship designs
    # next check for buildings etc that could be placed on queue regardless of locally available PP
    # next loop over resource groups, adding buildings & ships
    universe = fo.getUniverse()
    building_cache.update()
    capitol_id = PlanetUtilsAI.get_capital()
    if capitol_id is None or capitol_id == -1:
        homeworld = None
        capitol_sys_id = -1
    else:
        homeworld = universe.getPlanet(capitol_id)
        capitol_sys_id = homeworld.systemID
    print "Production Queue Management:"
    empire = fo.getEmpire()
    production_queue = empire.productionQueue
    total_pp = empire.productionPoints
    current_turn = fo.currentTurn()
    print
    print "  Total Available Production Points: " + str(total_pp)

    claimed_stars = foAI.foAIstate.misc.get('claimedStars', {})
    if claimed_stars == {}:
        for sType in AIstate.empireStars:
            claimed_stars[sType] = list(AIstate.empireStars[sType])
        for sys_id in set(AIstate.colonyTargetedSystemIDs + AIstate.outpostTargetedSystemIDs):
            target_sys = universe.getSystem(sys_id)
            if not target_sys:
                continue
            claimed_stars.setdefault(target_sys.starType, []).append(sys_id)

    # at beginning of the game, enqueue some scouts unless we already have found enemy planets
    if(current_turn < 5 and not AIstate.opponentPlanetIDs
       and (production_queue.totalSpent < total_pp or len(production_queue) <= 3)
       ):
        best_design_id, _, build_choices = get_best_ship_info(EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_EXPLORATION)
        if best_design_id:
            for scout_count in xrange(3):
                foAI.foAIstate.production_queue_manager.enqueue_item(SHIP, best_design_id, build_choices[0],
                                                                     PRIORITY_SHIP_SCOUT)
        fo.updateProductionQueue()

    bldg_expense = 0.0
    bldg_ratio = [0.4, 0.35, 0.30][fo.empireID() % 3]
    print "Buildings on owned planets:"
    for pid in (AIstate.popCtrIDs + AIstate.outpostIDs):
        planet = universe.getPlanet(pid)
        if planet:
            print "%30s: %s" % (planet.name, [universe.getObject(bldg).name for bldg in planet.buildingIDs])
    print

    existing_buildings = _get_all_existing_buildings()
    # technically, already printed all buildings but different point of view and to test dict integrity
    print "Locations of existing buildings:"
    for bld_name, locs in existing_buildings.iteritems():
        print "%s: " % bld_name,
        print [planet.name for planet in map(universe.getPlanet, locs) if planet]

    queued_buildings = foAI.foAIstate.production_queue_manager.get_all_queued_buildings()
    print "Enqueued buildings:"
    for bld_name, locs in queued_buildings.iteritems():
        print "%s: " % bld_name,
        print [planet.name for planet in map(universe.getPlanet, locs) if planet]

    if not homeworld:
        print "if no capitol, no place to build, should get around to capturing or colonizing a new one"  # TODO
    else:
        print "Empire ID %d has current Capital %s:" % (empire.empireID, homeworld.name)
        print "Buildings present at empire Capital (ID, Name, Type, Tags, Specials, OwnedbyEmpire):"
        for bldg in homeworld.buildingIDs:
            this_obj = universe.getObject(bldg)
            tags = ",".join(this_obj.tags)
            specials = ",".join(this_obj.specials)
            print "%8s | %20s | type:%20s | tags:%20s | specials: %20s | owner:%d " % (
                bldg, this_obj.name, "_".join(this_obj.buildingTypeName.split("_")[-2:])[:20],
                tags, specials, this_obj.owner)
        print
        capital_bldgs = [universe.getObject(bldg).buildingTypeName for bldg in homeworld.buildingIDs]

        possible_building_type_ids = []
        for bldTID in empire.availableBuildingTypes:
            try:
                if fo.getBuildingType(bldTID).canBeProduced(empire.empireID, homeworld.id):
                    possible_building_type_ids.append(bldTID)
            except Exception as e:
                if fo.getBuildingType(bldTID) is None:
                    print "For empire %d, 'availableBuildingTypeID' %s returns None from fo.getBuildingType(bldTID)" % (
                        empire.empireID, bldTID)
                else:
                    print "For empire %d, problem getting BuildingTypeID for 'available Building Type ID' %s" % (
                        empire.empireID, bldTID)
                print_error(e)
        if possible_building_type_ids:
            print "Possible building types to build:"
            for buildingTypeID in possible_building_type_ids:
                building_type = fo.getBuildingType(buildingTypeID)
                # print "building_type object:", building_type
                # print "dir(building_type): ", dir(building_type)
                print "    " + str(building_type.name) + " cost: " + str(
                    building_type.productionCost(empire.empireID, homeworld.id)) + " time: " + str(
                    building_type.productionTime(empire.empireID, homeworld.id))
                
            possible_building_types = [fo.getBuildingType(buildingTypeID) and fo.getBuildingType(buildingTypeID).name
                                       for buildingTypeID in possible_building_type_ids]
            print
            print "Buildings already in Production Queue:"
            capitol_queued_bldgs = []
            queued_exobot_locs = []
            for element in [e for e in production_queue if (e.buildType == EnumsAI.AIEmpireProductionTypes.BT_BUILDING)]:
                bldg_expense += element.allocation
                if element.locationID == homeworld.id:
                    capitol_queued_bldgs.append(element)
                if element.name == "BLD_COL_EXOBOT":
                    queued_exobot_locs.append(element.locationID)
            for bldg in capitol_queued_bldgs:
                print "    " + bldg.name + " turns:" + str(bldg.turnsLeft) + " PP:" + str(bldg.allocation)
            if not capitol_queued_bldgs:
                print "None"
            print
            queued_bldg_names = [bldg.name for bldg in capitol_queued_bldgs]

            if((total_pp > 40 or (current_turn > 40 and ColonisationAI.empire_status.get('industrialists', 0) >= 20))
               and "BLD_INDUSTRY_CENTER" in possible_building_types
               and "BLD_INDUSTRY_CENTER" not in (capital_bldgs + queued_bldg_names)
               and bldg_expense < bldg_ratio * total_pp
               ):
                res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, "BLD_INDUSTRY_CENTER",
                                                                           homeworld.id, PRIORITY_BUILDING_HIGH)
                if res:
                    cost, prod_time = empire.productionCostAndTime(production_queue[production_queue.size - 1])
                    bldg_expense += cost / prod_time

            if("BLD_SHIPYARD_BASE" in possible_building_types
               and "BLD_SHIPYARD_BASE" not in (capital_bldgs + queued_bldg_names)
               ):
                res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, "BLD_SHIPYARD_BASE",
                                                                           homeworld.id, PRIORITY_BUILDING_LOW)

            for bld_name in ["BLD_SHIPYARD_ORG_ORB_INC"]:
                if(bld_name in possible_building_types
                   and bld_name not in (capital_bldgs + queued_bldg_names)
                   and bldg_expense < bldg_ratio * total_pp
                   ):
                    try:
                        res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, bld_name, homeworld.id,
                                                                                   PRIORITY_BUILDING_LOW)
                        if res:
                            cost, prod_time = empire.productionCostAndTime(production_queue[production_queue.size - 1])
                            bldg_expense += cost / prod_time
                            print "Requeueing %s to front of build queue, with result %d" % (bld_name, res)
                    except Exception as e:
                        print_error(e)

            if("BLD_IMPERIAL_PALACE" in possible_building_types
               and "BLD_IMPERIAL_PALACE" not in (capital_bldgs + queued_bldg_names)
               ):
                res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, "BLD_IMPERIAL_PALACE",
                                                                           homeworld.id, PRIORITY_BUILDING_HIGH)

            # ok, BLD_NEUTRONIUM_SYNTH is not currently unlockable, but just in case... ;-p
            if("BLD_NEUTRONIUM_SYNTH" in possible_building_types
               and "BLD_NEUTRONIUM_SYNTH" not in (capital_bldgs + queued_bldg_names)
               ):
                res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, "BLD_NEUTRONIUM_SYNTH",
                                                                           homeworld.id, PRIORITY_BUILDING_LOW)
            # TODO: add total_pp checks below, so don't overload queue

    # best_pilot_locs = sorted([(rating, pid) for pid, rating in ColonisationAI.pilot_ratings.items()
    #                           if rating == ColonisationAI.get_best_pilot_rating()], reverse=True)
    best_pilot_facilities = ColonisationAI.facilities_by_species_grade.get(
        "WEAPONS_%.1f" % ColonisationAI.get_best_pilot_rating(), {})

    print "best_pilot_facilities: \n %s" % best_pilot_facilities

    max_defense_portion = [0.7, 0.4, 0.3, 0.2, 0.1, 0.0][foAI.foAIstate.aggression]
    aggression_index = max(1, foAI.foAIstate.aggression)
    if ((current_turn % aggression_index) == 0) and foAI.foAIstate.aggression < fo.aggression.maniacal:
        sys_orbital_defenses = {}
        queued_defenses = {}
        defense_allocation = 0.0
        target_orbitals = min(int(((current_turn + 4) / (8.0 * aggression_index ** 1.5)) ** 0.8),
                              fo.aggression.maniacal - aggression_index)
        print "Orbital Defense Check -- target Defense Orbitals: ", target_orbitals
        for element in production_queue:
            if(element.buildType == EnumsAI.AIEmpireProductionTypes.BT_SHIP
               and foAI.foAIstate.get_ship_role(element.designID) == EnumsAI.AIShipRoleType.SHIP_ROLE_BASE_DEFENSE
               ):
                bld_planet = universe.getPlanet(element.locationID)
                if not bld_planet:
                    print "Error: Problem getting Planet for build loc %s" % element.locationID
                    continue
                sys_id = bld_planet.systemID
                queued_defenses[sys_id] = queued_defenses.get(sys_id, 0) + element.blocksize * element.remaining
                defense_allocation += element.allocation
        print "Queued Defenses:", [(ppstring(PlanetUtilsAI.sys_name_ids([sys_id])), num) for sys_id, num in
                                   queued_defenses.items()]
        for sys_id in ColonisationAI.empire_species_systems:
            if foAI.foAIstate.systemStatus.get(sys_id, {}).get('fleetThreat', 1) > 0:
                continue  # don't build orbital shields if enemy fleet present
            if defense_allocation > max_defense_portion * total_pp:
                break
            # print "checking ", ppstring(PlanetUtilsAI.sys_name_ids([sys_id]))
            sys_orbital_defenses[sys_id] = 0
            fleets_here = foAI.foAIstate.systemStatus.get(sys_id, {}).get('myfleets', [])
            for fid in fleets_here:
                if foAI.foAIstate.get_fleet_role(fid) == EnumsAI.AIFleetMissionType.FLEET_MISSION_ORBITAL_DEFENSE:
                    print "Found %d existing Orbital Defenses in %s :" % (
                        foAI.foAIstate.fleetStatus.get(fid, {}).get('nships', 0),
                        ppstring(PlanetUtilsAI.sys_name_ids([sys_id])))
                    sys_orbital_defenses[sys_id] += foAI.foAIstate.fleetStatus.get(fid, {}).get('nships', 0)
            for pid in ColonisationAI.empire_species_systems.get(sys_id, {}).get('pids', []):
                sys_orbital_defenses[sys_id] += queued_defenses.get(pid, 0)
            if sys_orbital_defenses[sys_id] < target_orbitals:
                num_needed = target_orbitals - sys_orbital_defenses[sys_id]
                for pid in ColonisationAI.empire_species_systems.get(sys_id, {}).get('pids', []):
                    best_design_id, col_design, build_choices = get_best_ship_info(
                        EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_ORBITAL_DEFENSE, pid)
                    if not best_design_id:
                        print "no orbital defenses can be built at ", ppstring(PlanetUtilsAI.planet_name_ids([pid]))
                        continue
                    # print "selecting ", ppstring(PlanetUtilsAI.planet_name_ids([pid])), " to build Orbital Defenses"
                    for i in xrange(0, num_needed):
                        retval = foAI.foAIstate.production_queue_manager.enqueue_item(SHIP, best_design_id, pid,
                                                                                      PRIORITY_ORBITAL_DEFENSE)
                    print "queueing %d Orbital Defenses at %s" % (
                        num_needed, ppstring(PlanetUtilsAI.planet_name_ids([pid])))
                    if retval != 0:
                        cost, prod_time = empire.productionCostAndTime(production_queue[production_queue.size - 1])
                        defense_allocation += production_queue[production_queue.size - 1].blocksize * cost / prod_time
                        break

    bld_type = fo.getBuildingType("BLD_SHIPYARD_BASE")
    queued_shipyard_locs = [element.locationID for element in production_queue if (element.name == "BLD_SHIPYARD_BASE")]
    system_colonies = {}
    colony_systems = {}
    for specName in ColonisationAI.empire_colonizers:
        if(len(ColonisationAI.empire_colonizers[specName]) == 0
            and specName in ColonisationAI.empire_species
           ):  # not enough current shipyards for this species
            # TODO: also allow orbital incubators and/or asteroid ships
            # SP_EXOBOT may not actually have a colony yet but be in empireColonizers
            for pID in ColonisationAI.empire_species.get(specName, []):  
                if pID in queued_shipyard_locs:
                    break  # won't try building more than one shipyard at once, per colonizer
            else:  # no queued shipyards
                # get planets with target pop >=3, and queue a shipyard on the one with biggest current pop
                planet_list = zip(map(universe.getPlanet, ColonisationAI.empire_species[specName]),
                                  ColonisationAI.empire_species[specName])
                pops = sorted([(planet.currentMeterValue(fo.meterType.population), pID) for planet, pID in planet_list if
                               (planet and planet.currentMeterValue(fo.meterType.targetPopulation) >= 3.0)])
                pids = [pid for pop, pid in pops if bld_type.canBeProduced(empire.empireID, pid)]
                if pids:
                    build_loc = pids[-1]
                    res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, "BLD_SHIPYARD_BASE", build_loc,
                                                                               PRIORITY_BUILDING_LOW)
                    if res:
                        queued_shipyard_locs.append(build_loc)
                        break  # only start at most one new shipyard per species per turn
        for pid in ColonisationAI.empire_species.get(specName, []):
            planet = universe.getPlanet(pid)
            if planet:
                system_colonies.setdefault(planet.systemID, {}).setdefault('pids', []).append(pid)
                colony_systems[pid] = planet.systemID

    acirema_systems = {}
    for pid in ColonisationAI.empire_species.get("SP_ACIREMA", []):
        acirema_systems.setdefault(universe.getPlanet(pid).systemID, []).append(pid)
        if (pid in queued_shipyard_locs) or not bld_type.canBeProduced(empire.empireID, pid):
            continue  # but not 'break' because we want to build shipyards at *every* Acirema planet
        res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, "BLD_SHIPYARD_BASE", pid,
                                                                   PRIORITY_BUILDING_LOW)
        if res:
            queued_shipyard_locs.append(pid)

    top_pilot_systems = {}
    for pid, rating in ColonisationAI.pilot_ratings.items():
        if (rating <= ColonisationAI.get_medium_pilot_rating()) and (rating < ColonisationAI.GREAT_PILOT_RATING):
            continue
        top_pilot_systems.setdefault(universe.getPlanet(pid).systemID, []).append((pid, rating))
        if (pid in queued_shipyard_locs) or not bld_type.canBeProduced(empire.empireID, pid):
            continue  # but not 'break' because we want to build shipyards all top pilot planets
        res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, "BLD_SHIPYARD_BASE", pid,
                                                                   PRIORITY_BUILDING_LOW)
        if res:
            queued_shipyard_locs.append(pid)

    pop_ctrs = list(AIstate.popCtrIDs)
    red_popctrs = sorted([(ColonisationAI.pilot_ratings.get(pid, 0), pid) for pid in pop_ctrs
                          if colony_systems.get(pid, -1) in AIstate.empireStars.get(fo.starType.red, [])],
                         reverse=True)
    red_pilots = [pid for rating, pid in red_popctrs if rating == ColonisationAI.get_best_pilot_rating()]
    blue_popctrs = sorted([(ColonisationAI.pilot_ratings.get(pid, 0), pid) for pid in pop_ctrs
                           if colony_systems.get(pid, -1) in AIstate.empireStars.get(fo.starType.blue, [])],
                          reverse=True)
    blue_pilots = [pid for rating, pid in blue_popctrs if rating == ColonisationAI.get_best_pilot_rating()]
    bh_popctrs = sorted([(ColonisationAI.pilot_ratings.get(pid, 0), pid) for pid in pop_ctrs
                         if colony_systems.get(pid, -1) in AIstate.empireStars.get(fo.starType.blackHole, [])],
                        reverse=True)
    bh_pilots = [pid for rating, pid in bh_popctrs if rating == ColonisationAI.get_best_pilot_rating()]
    energy_shipyard_locs = {}
    for bld_name in ["BLD_SHIPYARD_ENRG_COMP"]:
        if empire.buildingTypeAvailable(bld_name):
            queued_bld_locs = [element.locationID for element in production_queue if (element.name == bld_name)]
            bld_type = fo.getBuildingType(bld_name)
            for pid in bh_pilots + blue_pilots:
                if len(queued_bld_locs) > 1:  # build a max of 2 at once
                    break
                this_planet = universe.getPlanet(pid)
                # TODO: also check that not already one for this spec in this sys
                if not (this_planet and this_planet.speciesName in ColonisationAI.empire_ship_builders):
                    continue
                energy_shipyard_locs.setdefault(this_planet.systemID, []).append(pid)
                if pid not in queued_bld_locs and bld_type.canBeProduced(empire.empireID, pid):
                    res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, bld_name, pid,
                                                                               PRIORITY_BUILDING_LOW)
                    if _CHAT_DEBUG:
                        chat_human(
                            "Enqueueing %s at planet %s, with result %d" % (bld_name, universe.getPlanet(pid), res))
                    if res:
                        queued_bld_locs.append(pid)
                        cost, prod_time = empire.productionCostAndTime(production_queue[production_queue.size - 1])
                        bldg_expense += cost / prod_time  # production_queue[production_queue.size -1].blocksize *

    bld_name = "BLD_SHIPYARD_ENRG_SOLAR"
    queued_bld_locs = [element.locationID for element in production_queue if (element.name == bld_name)]
    if empire.buildingTypeAvailable(bld_name) and not queued_bld_locs:
        # TODO: check that production is not frozen at a queued location
        bld_type = fo.getBuildingType(bld_name)
        for pid in bh_pilots:
            this_planet = universe.getPlanet(pid)
            # TODO: also check that not already one for this spec in this sys
            if not (this_planet and this_planet.speciesName in ColonisationAI.empire_ship_builders):
                continue
            if bld_type.canBeProduced(empire.empireID, pid):
                res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, bld_name, pid,
                                                                           PRIORITY_BUILDING_LOW)
                if _CHAT_DEBUG:
                    chat_human("Enqueueing %s at planet %s, with result %d" % (bld_name, universe.getPlanet(pid), res))
                if res:
                    cost, prod_time = empire.productionCostAndTime(production_queue[production_queue.size - 1])
                    bldg_expense += cost / prod_time  # production_queue[production_queue.size -1].blocksize *
                    break

    bld_name = "BLD_SHIPYARD_BASE"
    if(empire.buildingTypeAvailable(bld_name)
       and bldg_expense < bldg_ratio * total_pp
       and (total_pp > 50 or current_turn > 80)
       ):
        bld_type = fo.getBuildingType(bld_name)
        for sys_id in energy_shipyard_locs:  # Todo ensure only one or 2 per sys
            for pid in energy_shipyard_locs[sys_id][:2]:
                # TODO: verify that canBeProduced() checks for prexistence of a barring building
                if pid not in queued_shipyard_locs and bld_type.canBeProduced(empire.empireID, pid):
                    res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, bld_name, pid,
                                                                               PRIORITY_BUILDING_LOW)
                    if res:
                        queued_shipyard_locs.append(pid)
                        cost, prod_time = empire.productionCostAndTime(production_queue[production_queue.size - 1])
                        bldg_expense += cost / prod_time  # production_queue[production_queue.size -1].blocksize *
                        break  # only start one per turn

    shipyard_list = [
        "BLD_SHIPYARD_CON_GEOINT",
        "BLD_SHIPYARD_AST_REF",
        "BLD_SHIPYARD_ORG_ORB_INC",
        ]
    for bld_name in shipyard_list:
        build_ship_facilities(bld_name, best_pilot_facilities)

    bld_name = "BLD_NEUTRONIUM_FORGE"
    priority_facilities = ["BLD_SHIPYARD_ENRG_SOLAR",
                           "BLD_SHIPYARD_CON_GEOINT",
                           "BLD_SHIPYARD_AST_REF",
                           "BLD_SHIPYARD_ENRG_COMP"]
    # TODO: also cover good troopship locations
    # not a problem if locs appear multiple times here
    top_locs = [loc for facil in priority_facilities for loc in best_pilot_facilities.get(facil, [])]
    build_ship_facilities(bld_name, best_pilot_facilities, top_locs)

    # gating by life cycle manipulation helps delay these until they are closer to being worthwhile
    if tech_is_complete(AIDependencies.GRO_LIFE_CYCLE) or empire.researchProgress(AIDependencies.GRO_LIFE_CYCLE) > 0:
        for bld_name in ["BLD_SHIPYARD_ORG_XENO_FAC", "BLD_SHIPYARD_ORG_CELL_GRO_CHAMB"]:
            build_ship_facilities(bld_name, best_pilot_facilities)

    shipyard_type = fo.getBuildingType("BLD_SHIPYARD_BASE")
    bld_name = "BLD_SHIPYARD_AST"
    if empire.buildingTypeAvailable(bld_name) and foAI.foAIstate.aggression > fo.aggression.beginner:
        queued_bld_locs = [element.locationID for element in production_queue if (element.name == bld_name)]
        if not queued_bld_locs:
            bld_type = fo.getBuildingType(bld_name)
            asteroid_systems = {}
            asteroid_yards = {}
            shipyard_systems = {}
            builder_systems = {}
            for pid in list(AIstate.popCtrIDs) + list(AIstate.outpostIDs):
                planet = universe.getPlanet(pid)
                this_spec = planet.speciesName
                sys_id = planet.systemID
                if planet.size == fo.planetSize.asteroids and sys_id in ColonisationAI.empire_species_systems:
                    asteroid_systems.setdefault(sys_id, []).append(pid)
                    if(pid in queued_bld_locs
                       or bld_name in [universe.getObject(bldg).buildingTypeName for bldg in planet.buildingIDs]
                       ):
                        asteroid_yards[sys_id] = pid  # shouldn't ever overwrite another, but ok if it did
                if this_spec in ColonisationAI.empire_ship_builders:
                    if pid in ColonisationAI.empire_ship_builders[this_spec]:
                        shipyard_systems.setdefault(sys_id, []).append(pid)
                    else:
                        builder_systems.setdefault(sys_id, []).append((planet.speciesName, pid))
            # check if we need to build another asteroid processor:
            # check if local shipyard to go with the asteroid processor
            yard_locs = []
            need_yard = {}
            top_pilot_locs = []
            for sys_id in set(asteroid_systems.keys()).difference(asteroid_yards.keys()):
                if sys_id in top_pilot_systems:
                    for pid, rating in top_pilot_systems[sys_id]:
                        if pid not in queued_shipyard_locs:  # will catch it later if shipyard already present
                            top_pilot_locs.append((rating, pid, sys_id))
            top_pilot_locs.sort(reverse=True)
            for rating, pid, sys_id in top_pilot_locs:
                if sys_id not in yard_locs:
                    yard_locs.append(sys_id)  # prioritize asteroid yards for acirema and/or other top pilots
                    for pid2, rating2 in top_pilot_systems[sys_id]:
                        if pid2 not in queued_shipyard_locs:  # will catch it later if shipyard already present
                            need_yard[sys_id] = pid2
            if (not yard_locs) and len(asteroid_yards.values()) <= int(current_turn / 50):
                # not yet building & not enough current locs, find a location to build one
                # queuedYardSystems = set(PlanetUtilsAI.get_systems(queued_shipyard_locs))
                colonizer_loc_choices = []
                builder_loc_choices = []
                bld_systems = set(asteroid_systems.keys()).difference(asteroid_yards.keys())
                for sys_id in bld_systems.intersection(builder_systems.keys()):
                    for this_spec, pid in builder_systems[sys_id]:
                        if this_spec in ColonisationAI.empire_colonizers:
                            if pid in (ColonisationAI.empire_colonizers[this_spec] + queued_shipyard_locs):
                                colonizer_loc_choices.insert(0, sys_id)
                            else:
                                colonizer_loc_choices.append(sys_id)
                                need_yard[sys_id] = pid
                        else:
                            if pid in (ColonisationAI.empire_ship_builders.get(this_spec, []) + queued_shipyard_locs):
                                builder_loc_choices.insert(0, sys_id)
                            else:
                                builder_loc_choices.append(sys_id)
                                need_yard[sys_id] = pid
                yard_locs.extend(
                    (colonizer_loc_choices + builder_loc_choices)[:1])  # add at most one of these non top pilot locs
            new_yard_count = len(queued_bld_locs)
            for sys_id in yard_locs:  # build at most 2 new asteroid yards at a time
                if new_yard_count >= 2:
                    break
                pid = asteroid_systems[sys_id][0]
                if sys_id in need_yard:
                    pid2 = need_yard[sys_id]
                    if shipyard_type.canBeProduced(empire.empireID, pid2):
                        res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, "BLD_SHIPYARD_BASE", pid2,
                                                                                   PRIORITY_BUILDING_LOW)
                        if res:
                            queued_shipyard_locs.append(pid2)
                            cost, prod_time = empire.productionCostAndTime(production_queue[production_queue.size - 1])
                            bldg_expense += cost / prod_time  # production_queue[production_queue.size -1].blocksize *
                if pid not in queued_bld_locs and bld_type.canBeProduced(empire.empireID, pid):
                    res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, bld_name, pid,
                                                                               PRIORITY_BUILDING_LOW)
                    if res:
                        new_yard_count += 1
                        queued_bld_locs.append(pid)
                        cost, prod_time = empire.productionCostAndTime(production_queue[production_queue.size - 1])
                        bldg_expense += cost / prod_time  # production_queue[production_queue.size -1].blocksize *

    bld_name = "BLD_GAS_GIANT_GEN"
    max_gggs = 1
    if empire.buildingTypeAvailable(bld_name) and foAI.foAIstate.aggression > fo.aggression.beginner:
        queued_bld_locs = [element.locationID for element in production_queue if (element.name == bld_name)]
        bld_type = fo.getBuildingType(bld_name)
        for pid in list(AIstate.popCtrIDs) + list(AIstate.outpostIDs):
            # TODO: check to ensure that a resource center exists in system, or GGG would be wasted
            if pid not in queued_bld_locs and bld_type.canBeProduced(empire.empireID, pid):
                # TODO: verify that canBeProduced() checks for prexistence of a barring building
                this_planet = universe.getPlanet(pid)
                if this_planet.systemID in ColonisationAI.empire_species_systems:
                    gg_list = []
                    can_use_ggg = False
                    system = universe.getSystem(this_planet.systemID)
                    for opid in system.planetIDs:
                        other_planet = universe.getPlanet(opid)
                        if other_planet.size == fo.planetSize.gasGiant:
                            gg_list.append(opid)
                        if(opid != pid
                           and other_planet.owner == empire.empireID
                           and EnumsAI.AIFocusType.FOCUS_INDUSTRY in (list(other_planet.availableFoci)
                                                                      + [other_planet.focus])
                           ):
                            can_use_ggg = True
                    if pid in sorted(gg_list)[:max_gggs] and can_use_ggg:
                        res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, bld_name, pid,
                                                                                   PRIORITY_BUILDING_HIGH)
                        if res:
                            queued_bld_locs.append(pid)
                            cost, prod_time = empire.productionCostAndTime(production_queue[production_queue.size - 1])
                            bldg_expense += cost / prod_time  # production_queue[production_queue.size -1].blocksize *

    bld_name = "BLD_SOL_ORB_GEN"
    if empire.buildingTypeAvailable(bld_name) and foAI.foAIstate.aggression > fo.aggression.turtle:
        already_got_one = 99
        for pid in list(AIstate.popCtrIDs) + list(AIstate.outpostIDs):
            planet = universe.getPlanet(pid)
            if planet and bld_name in [bld.buildingTypeName for bld in map(universe.getObject, planet.buildingIDs)]:
                system = universe.getSystem(planet.systemID)
                if system and system.starType < already_got_one:
                    already_got_one = system.starType
        best_type = fo.starType.white
        best_locs = AIstate.empireStars.get(fo.starType.blue, []) + AIstate.empireStars.get(fo.starType.white, [])
        if not best_locs:
            best_type = fo.starType.orange
            best_locs = AIstate.empireStars.get(fo.starType.yellow, []) + AIstate.empireStars.get(fo.starType.orange,
                                                                                                  [])
        if (not best_locs) or (already_got_one < 99 and already_got_one <= best_type):
            pass  # could consider building at a red star if have a lot of PP but somehow no better stars
        else:
            use_new_loc = True
            queued_bld_locs = [element.locationID for element in production_queue if (element.name == bld_name)]
            if queued_bld_locs:
                queued_star_types = {}
                for loc in queued_bld_locs:
                    planet = universe.getPlanet(loc)
                    if not planet:
                        continue
                    system = universe.getSystem(planet.systemID)
                    queued_star_types.setdefault(system.starType, []).append(loc)
                if queued_star_types:
                    best_queued = sorted(queued_star_types.keys())[0]
                    if best_queued > best_type:  # i.e., best_queued is yellow, best_type available is blue or white
                        pass  # should probably evaluate cancelling the existing one under construction
                    else:
                        use_new_loc = False
            if use_new_loc:  # (of course, may be only loc, not really new)
                if not homeworld:
                    use_sys = best_locs[0]  # as good as any
                else:
                    use_sys, _ = _get_system_closest_to_target(best_locs, capitol_sys_id)
                if use_sys != -1:
                    use_loc = AIstate.colonizedSystems[use_sys][0]
                    res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, bld_name, use_loc,
                                                                               PRIORITY_BUILDING_HIGH)
                    if res:
                        cost, prod_time = empire.productionCostAndTime(production_queue[production_queue.size - 1])
                        bldg_expense += cost / prod_time  # production_queue[production_queue.size -1].blocksize *

    bld_name = "BLD_ART_BLACK_HOLE"
    min_aggression = fo.aggression.typical
    red_star_systems = AIstate.empireStars.get(fo.starType.red, [])
    if empire.buildingTypeAvailable(bld_name) and red_star_systems and foAI.foAIstate.aggression > min_aggression:
        existing_locs = existing_buildings.get(bld_name, []) + queued_buildings.get(bld_name, [])
        valid_existing_locs = set(red_star_systems).intersection(existing_locs)
        if not valid_existing_locs:  # only have one at a time
            black_hole_systems = AIstate.empireStars.get(fo.starType.blackHole, [])
            black_hole_generator = "BLD_BLACK_HOLE_POW_GEN"
            already_queued_one = False
            # first, check if we need some black hole to build a solar hull
            if not bh_pilots and red_pilots and "SH_SOLAR" in empire.availableShipHulls:  # TODO: generalize hulls
                print "Considering to build a %s so we get access to black hole pilots (for solar hulls)." % bld_name
                use_loc = -1
                # we try to find a location where we have a suitable piloting species in the system
                # but we also need to make sure that we do not kill of our phototropic species in the system
                # TODO: Implement scenarios where we allow to kill phototropic species for the greater good.
                for pid in red_pilots:
                    # find all of our planets in the same system and check for phototropic species
                    planet = universe.getPlanet(pid)
                    if not planet:
                        continue
                    for pid2 in PlanetUtilsAI.get_empire_planets_in_system(planet.systemID):
                        planet2 = universe.getPlanet(pid2)
                        if planet2 and planet2.speciesName:
                            species = fo.getSpecies(planet2.speciesName)
                            if species and "PHOTOTROPHIC" in species.tags:
                                break
                    else:  # no phototrophic species in this system; safe to create a black hole
                        use_loc = pid
                        break
                if use_loc != -1:
                    res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, bld_name, use_loc,
                                                                               PRIORITY_BUILDING_BASE)
                    already_queued_one = True  # even if some error occurs, do not try to build another one...
                else:
                    print "But could not find a suitable location..."
            # now check if we need a black hole to build the black hole power generator
            if not already_queued_one and empire.buildingTypeAvailable(black_hole_generator) and not black_hole_systems:
                print "Considering to build a %s so we can build a %s." % (bld_name, black_hole_generator)
                use_sys, _ = _get_system_closest_to_target(red_star_systems, capitol_sys_id)
                if use_sys != -1:
                    use_loc = AIstate.colonizedSystems[use_sys][0]
                    res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, bld_name, use_loc,
                                                                               PRIORITY_BUILDING_HIGH)
                else:
                    print "But could not find a suitable location..."

    bld_name = "BLD_BLACK_HOLE_POW_GEN"
    if empire.buildingTypeAvailable(bld_name) and foAI.foAIstate.aggression > fo.aggression.cautious:
        already_got_one = bld_name in existing_buildings
        already_queued_one = bld_name in queued_buildings
        black_hole_systems = AIstate.empireStars.get(fo.starType.blackHole, [])
        if black_hole_systems and not (already_got_one or already_queued_one):
            if not homeworld:
                use_sys = black_hole_systems[0]
            else:
                use_sys, _ = _get_system_closest_to_target(black_hole_systems, capitol_sys_id)
            if use_sys != -1:
                use_loc = AIstate.colonizedSystems[use_sys][0]
                res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, bld_name, use_loc,
                                                                           PRIORITY_BUILDING_HIGH)

    # buildings that we always want to build but only once...
    unconditional_unique_buildings = [
        # (bld_name, priority)
        ("BLD_ENCLAVE_VOID", PRIORITY_BUILDING_HIGH),
        ("BLD_GENOME_BANK", PRIORITY_BUILDING_LOW),
    ]
    if homeworld:
        for bld_name, priority in unconditional_unique_buildings:
            if empire.buildingTypeAvailable(bld_name):
                already_got_one = bld_name in existing_buildings
                already_queued_one = bld_name in queued_buildings
                if not (already_got_one or already_queued_one):
                    res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, bld_name, capitol_id, priority)

    bld_name = "BLD_NEUTRONIUM_EXTRACTOR"
    if empire.buildingTypeAvailable(bld_name):
        # valid planets are either in a neutron star system or where we have a neutronium synthetizer
        planets_with_neutron = set(AIstate.outpostIDs + AIstate.popCtrIDs).intersection(
            PlanetUtilsAI.get_planets_in__systems_ids(AIstate.empireStars.get(fo.starType.neutron, [])))
        planets_with_neutron.update(existing_buildings.get("BLD_NEUTRONIUM_SYNTH", []))

        already_got_extractor = len(planets_with_neutron.intersection(existing_buildings.get(bld_name, [])
                                                                      + queued_buildings.get(bld_name, []))) > 0
        if planets_with_neutron and not already_got_extractor:
            print "Trying to find a suitable location for %s" % bld_name
            neutron_systems = PlanetUtilsAI.get_systems(planets_with_neutron)  # non-empty: we have planets_with_neutron
            if not homeworld:
                use_sys = neutron_systems[0]
            else:
                use_sys, _ = _get_system_closest_to_target(neutron_systems, capitol_sys_id)
            if use_sys != -1:
                use_loc = AIstate.colonizedSystems[use_sys][0]
                res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, bld_name, use_loc,
                                                                           PRIORITY_BUILDING_HIGH)

    colony_ship_map = {}
    for fid in FleetUtilsAI.get_empire_fleet_ids_by_role(EnumsAI.AIFleetMissionType.FLEET_MISSION_COLONISATION):
        fleet = universe.getFleet(fid)
        if not fleet:
            continue
        for shipID in fleet.shipIDs:
            this_ship = universe.getShip(shipID)
            if this_ship and (foAI.foAIstate.get_ship_role(
                    this_ship.design.id) == EnumsAI.AIShipRoleType.SHIP_ROLE_CIVILIAN_COLONISATION):
                colony_ship_map.setdefault(this_ship.speciesName, []).append(1)

    bld_name = "BLD_CONC_CAMP"
    verbose_camp = False
    bld_type = fo.getBuildingType(bld_name)
    for pid in AIstate.popCtrIDs:
        planet = universe.getPlanet(pid)
        if not planet:
            continue
        can_build_camp = bld_type.canBeProduced(empire.empireID, pid) and empire.buildingTypeAvailable(bld_name)
        target_pop = planet.currentMeterValue(fo.meterType.targetPopulation)
        target_ind = planet.currentMeterValue(fo.meterType.targetIndustry)
        current_ind = planet.currentMeterValue(fo.meterType.industry)
        current_pop = planet.currentMeterValue(fo.meterType.population)
        pop_disqualified = current_pop <= 32 or current_pop < 0.9 * target_pop
        built_camp = False
        this_spec = planet.speciesName
        safety_margin_met = ((this_spec in ColonisationAI.empire_colonizers and (
            len(ColonisationAI.empire_species.get(this_spec, []) + colony_ship_map.get(this_spec, [])) >= 2)) or (current_pop >= 50))
        has_camp = pid in existing_buildings.get(bld_name, [])
        if pop_disqualified or not safety_margin_met:
            # check even if not aggressive, etc, just in case acquired planet with a ConcCamp on it
            if can_build_camp:
                if pop_disqualified:
                    if verbose_camp:
                        print "Conc Camp disqualified at %s due to low pop: current %.1f target: %.1f" % (
                            planet.name, current_pop, target_pop)
                elif verbose_camp:
                    print "Conc Camp disqualified at %s due to safety margin; species %s, colonizing planets %s, with %d colony ships" % (
                        planet.name, planet.speciesName, ColonisationAI.empire_species.get(planet.speciesName, []),
                        len(colony_ship_map.get(planet.speciesName, [])))
            if has_camp:
                res = fo.issueScrapOrder(bldg)
                print "Tried scrapping %s at planet %s, got result %d" % (bld_name, planet.name, res)
        elif foAI.foAIstate.aggression > fo.aggression.typical and can_build_camp and (target_pop >= 36) and not has_camp:
            if(planet.focus == EnumsAI.AIFocusType.FOCUS_GROWTH
               or "COMPUTRONIUM_SPECIAL" in planet.specials
               or pid == capitol_id
               ):
                continue  # now that focus setting takes these into account, probably works ok to have conc camp
               
            queued_bld_locs = queued_buildings.get(bld_name, [])
            if current_pop < 0.95 * target_pop:  #
                if verbose_camp:
                    print "Conc Camp disqualified at %s due to pop: current %.1f target: %.1f" % (
                        planet.name, current_pop, target_pop)
            else:
                if pid not in queued_bld_locs:
                    if planet.focus in [EnumsAI.AIFocusType.FOCUS_INDUSTRY]:
                        if current_ind >= target_ind + current_pop:
                            continue
                    else:
                        old_focus = planet.focus
                        fo.issueChangeFocusOrder(pid, EnumsAI.AIFocusType.FOCUS_INDUSTRY)
                        universe.updateMeterEstimates([pid])
                        target_ind = planet.currentMeterValue(fo.meterType.targetIndustry)
                        if current_ind >= target_ind + current_pop:
                            fo.issueChangeFocusOrder(pid, old_focus)
                            universe.updateMeterEstimates([pid])
                            continue
                    res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, bld_name, pid,
                                                                               PRIORITY_BUILDING_HIGH)
                    built_camp = res
                    if res:
                        queued_bld_locs.append(pid)
        if verbose_camp:
            print "conc camp status at %s : checkedCamp: %s, built_camp: %s" % (planet.name, can_build_camp, built_camp)

    bld_name = "BLD_SCANNING_FACILITY"
    if empire.buildingTypeAvailable(bld_name):
        queued_locs = queued_buildings.get(bld_name, [])
        existing_locs = existing_buildings.get(bld_name, [])
        scanner_locs = {}
        for system_id in PlanetUtilsAI.get_systems(queued_locs + existing_locs):
            scanner_locs[system_id] = True
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
                res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, bld_name, loc,
                                                                           PRIORITY_BUILDING_LOW)
                if res:
                    queued_locs.append(planet.systemID)
                    break

    bld_name = "BLD_SHIPYARD_ORBITAL_DRYDOCK"
    if empire.buildingTypeAvailable(bld_name):
        queued_locs = queued_buildings.get(bld_name, [])
        queued_sys = set(PlanetUtilsAI.get_systems(queued_locs))
        cur_drydoc_sys = set(ColonisationAI.empire_dry_docks.keys()).union(queued_sys)
        covered_drydoc_locs = set()
        for start_set, dest_set in [(cur_drydoc_sys, covered_drydoc_locs),
                                    (covered_drydoc_locs,
                                     covered_drydoc_locs)]:  # coverage of neighbors up to 2 jumps away from a drydock
            for dd_sys_id in start_set.copy():
                dest_set.add(dd_sys_id)
                dd_neighbors = dict_from_map(universe.getSystemNeighborsMap(dd_sys_id, empire.empireID))
                dest_set.update(dd_neighbors.keys())

        max_dock_builds = int(0.8 + empire.productionPoints / 120.0)
        print "Considering building %s, found current and queued systems %s" % (
            bld_name, ppstring(PlanetUtilsAI.sys_name_ids(cur_drydoc_sys.union(queued_sys))))
        # print "Empire shipyards found at %s"%(ppstring(PlanetUtilsAI.planet_name_ids(ColonisationAI.empireShipyards)))
        for sys_id, pids_dict in ColonisationAI.empire_species_systems.items():  # TODO: sort/prioritize in some fashion
            pids = pids_dict.get('pids', [])
            local_top_pilots = dict(top_pilot_systems.get(sys_id, []))
            local_drydocks = ColonisationAI.empire_dry_docks.get(sys_id, [])
            if len(queued_locs) >= max_dock_builds:
                print "Drydock enqueing halted with %d of max %d" % (len(queued_locs), max_dock_builds)
                break
            if (sys_id in covered_drydoc_locs) and not local_top_pilots:
                continue
            for _, pid in sorted([(local_top_pilots.get(pid, 0), pid) for pid in pids], reverse=True):
                # print "checking planet '%s'"%pid
                if pid not in ColonisationAI.empire_shipyards:
                    # print "Planet %s not in empireShipyards"%(ppstring(PlanetUtilsAI.planet_name_ids([pid])))
                    continue
                if pid in local_drydocks or pid in queued_locs:
                    break
                planet = universe.getPlanet(pid)
                res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, bld_name, pid,
                                                                           PRIORITY_BUILDING_LOW)
                if res:
                    queued_locs.append(planet.systemID)
                    covered_drydoc_locs.add(planet.systemID)
                    dd_neighbors = dict_from_map(universe.getSystemNeighborsMap(planet.systemID, empire.empireID))
                    covered_drydoc_locs.update(dd_neighbors.keys())
                    break
                else:
                    print "Error failed enqueueing %s at planet %d (%s) , with result %d" % (
                        bld_name, pid, planet.name, res)

    queued_clny_bld_locs = [element.locationID for element in production_queue if element.name.startswith('BLD_COL_')]
    colony_bldg_entries = ([entry for entry in foAI.foAIstate.colonisablePlanetIDs.items() if entry[1][0] > 60 and
                            entry[0] not in queued_clny_bld_locs and entry[0] in ColonisationAI.empire_outpost_ids]
                           [:PriorityAI.allottedColonyTargets + 2])
    for entry in colony_bldg_entries:
        pid = entry[0]
        bld_name = "BLD_COL_" + entry[1][1][3:]
        planet = universe.getPlanet(pid)
        # print "Checking %s at %s" % (bld_name, planet)
        bld_type = fo.getBuildingType(bld_name)
        if not (bld_type and bld_type.canBeEnqueued(empire.empireID, pid)):
            continue
        res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, bld_name, pid, PRIORITY_BUILDING_HIGH)
        if res:
            break
        else:
            print "Error failed enqueueing %s at planet %d (%s) , with result %d" % (bld_name, pid, planet.name, res)

    bld_name = "BLD_EVACUATION"
    for pid in AIstate.popCtrIDs:
        planet = universe.getPlanet(pid)
        if not planet:
            continue
        for bldg in planet.buildingIDs:
            if universe.getObject(bldg).buildingTypeName == bld_name:
                res = fo.issueScrapOrder(bldg)
                print "Tried scrapping %s at planet %s, got result %d" % (bld_name, planet.name, res)

    total_pp_spent = fo.getEmpire().productionQueue.totalSpent
    print "  Total Production Points Spent: " + str(total_pp_spent)

    wasted_pp = max(0, total_pp - total_pp_spent)
    print "  Wasted Production Points: " + str(wasted_pp)  # TODO: add resource group analysis
    avail_pp = total_pp - total_pp_spent - 0.0001

    print
    print "Projects already in Production Queue:"
    production_queue = empire.productionQueue
    print "production summary: %s" % [elem.name for elem in production_queue]
    queued_col_ships = {}
    queued_outpost_ships = 0
    queued_troop_ships = 0

    # TODO: blocked items might not need dequeuing, but rather for supply lines to be un-blockaded
    dequeue_list = []
    fo.updateProductionQueue()
    can_prioritize_troops = False
    for queue_index in range(len(production_queue)):
        element = production_queue[queue_index]
        block_str = "%d x " % element.blocksize
        print "    " + block_str + element.name + " requiring " + str(element.turnsLeft) +\
              " more turns; alloc: %.2f PP" % element.allocation + " with cum. progress of %.1f" % element.progress\
              + " being built at " + universe.getObject(element.locationID).name
        if element.turnsLeft == -1:
            if element.locationID not in AIstate.popCtrIDs + AIstate.outpostIDs:
                # dequeue_list.append(queue_index) #TODO add assessment of recapture -- invasion target etc.
                print "element %s will never be completed as stands and location %d no longer owned;" % (
                    element.name, element.locationID),
            else:
                print "element %s is projected to never be completed, but will remain on queue " % element.name
        elif element.buildType == EnumsAI.AIEmpireProductionTypes.BT_SHIP:
            this_role = foAI.foAIstate.get_ship_role(element.designID)
            if this_role == EnumsAI.AIShipRoleType.SHIP_ROLE_CIVILIAN_COLONISATION:
                this_spec = universe.getPlanet(element.locationID).speciesName
                queued_col_ships[this_spec] = queued_col_ships.get(this_spec, 0) + element.remaining * element.blocksize
            elif this_role == EnumsAI.AIShipRoleType.SHIP_ROLE_CIVILIAN_OUTPOST:
                queued_outpost_ships += element.remaining * element.blocksize
            elif this_role == EnumsAI.AIShipRoleType.SHIP_ROLE_BASE_OUTPOST:
                queued_outpost_ships += element.remaining * element.blocksize
            elif this_role == EnumsAI.AIShipRoleType.SHIP_ROLE_MILITARY_INVASION:
                queued_troop_ships += element.remaining * element.blocksize
            elif (this_role == EnumsAI.AIShipRoleType.SHIP_ROLE_CIVILIAN_EXPLORATION) and (queue_index <= 1):
                if len(AIstate.opponentPlanetIDs) > 0:
                    can_prioritize_troops = True
    if queued_col_ships:
        print "\nFound colony ships in build queue: %s" % queued_col_ships
    if queued_outpost_ships:
        print "\nFound outpost ships and bases in build queue: %s" % queued_outpost_ships

    for queue_index in dequeue_list[::-1]:
        foAI.foAIstate.production_queue_manager.dequeue_item_by_index(queue_index)

    all_military_fleet_ids = FleetUtilsAI.get_empire_fleet_ids_by_role(EnumsAI.AIFleetMissionType.FLEET_MISSION_MILITARY)
    n_military_total = sum([foAI.foAIstate.fleetStatus.get(fid, {}).get('nships', 0) for fid in all_military_fleet_ids])
    all_troop_fleet_ids = FleetUtilsAI.get_empire_fleet_ids_by_role(EnumsAI.AIFleetMissionType.FLEET_MISSION_INVASION)
    n_troop_total = sum([foAI.foAIstate.fleetStatus.get(fid, {}).get('nships', 0) for fid in all_troop_fleet_ids])
    av_troop_fleet_ids = list(FleetUtilsAI.extract_fleet_ids_without_mission_types(all_troop_fleet_ids))
    n_avail_troop_total = sum([foAI.foAIstate.fleetStatus.get(fid, {}).get('nships', 0) for fid in av_troop_fleet_ids])
    print "Trooper Status turn %d: %d total, with %d unassigned. %d queued, compared to %d total Military Ships" % (
        current_turn, n_troop_total, n_avail_troop_total, queued_troop_ships, n_military_total)
    if(capitol_id is not None
       and ((current_turn >= 40) or can_prioritize_troops)
       and foAI.foAIstate.systemStatus.get(capitol_sys_id, {}).get('fleetThreat', 0) == 0
       and foAI.foAIstate.systemStatus.get(capitol_sys_id, {}).get('neighborThreat', 0) == 0
       ):
        best_design_id, best_design, build_choices = get_best_ship_info(EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_INVASION)
        if build_choices is not None and len(build_choices) > 0:
            loc = random.choice(build_choices)
            prod_time = best_design.productionTime(empire.empireID, loc)
            prod_cost = best_design.productionCost(empire.empireID, loc)
            troopers_needed_forcing = max(0, int(
                min(0.99 + (current_turn / 20.0 - n_avail_troop_total) / max(2, prod_time - 1), n_military_total / 3 - n_troop_total)))
            num_ships = troopers_needed_forcing
            per_turn_cost = (float(prod_cost) / prod_time)
            if(troopers_needed_forcing > 0
               and total_pp > 3 * per_turn_cost * queued_troop_ships
               and foAI.foAIstate.aggression >= fo.aggression.typical
               ):
                for i in xrange(0, num_ships):
                    retval = foAI.foAIstate.production_queue_manager.enqueue_item(SHIP, best_design_id, loc,
                                                                                  PRIORITY_SHIP_TROOPS*PRIORITY_EMERGENCY_FACTOR)
                if retval != 0:
                    print "forcing %d new ship(s) to production queue: %s; per turn production cost %.1f" % (
                        num_ships, best_design.name(True), num_ships * per_turn_cost)
                    print
                    avail_pp -= num_ships * per_turn_cost
                    fo.updateProductionQueue()
        print

    print
    # get the highest production priorities
    production_priorities = {}
    for priorityType in EnumsAI.get_priority_production_types():
        production_priorities[priorityType] = int(max(0, (foAI.foAIstate.get_priority(priorityType)) ** 0.5))

    sorted_priorities = production_priorities.items()
    sorted_priorities.sort(lambda x, y: cmp(x[1], y[1]), reverse=True)

    topscore = -1
    num_colony_fleets = len(FleetUtilsAI.get_empire_fleet_ids_by_role(
        EnumsAI.AIFleetMissionType.FLEET_MISSION_COLONISATION))  # counting existing colony fleets each as one ship
    tot_colony_fleets = sum(queued_col_ships.values()) + num_colony_fleets
    num_outpost_fleets = len(FleetUtilsAI.get_empire_fleet_ids_by_role(
        EnumsAI.AIFleetMissionType.FLEET_MISSION_OUTPOST))  # counting existing outpost fleets each as one ship
    tot_outpost_fleets = queued_outpost_ships + num_outpost_fleets

    # max_colony_fleets = max(min(numColonyTargs+1+current_turn/10 , numTotalFleets/4), 3+int(3*len(empireColonizers)))
    # max_outpost_fleets = min(numOutpostTargs+1+current_turn/10, numTotalFleets/4)
    max_colony_fleets = PriorityAI.allottedColonyTargets
    max_outpost_fleets = max_colony_fleets

    _, _, colony_build_choices = get_best_ship_info(EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_COLONISATION)
    military_emergency = PriorityAI.unmetThreat > (2.0 * MilitaryAI.totMilRating)

    print "Production Queue Priorities:"
    filtered_priorities = {}
    for ID, score in sorted_priorities:
        if military_emergency:
            if ID == EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_EXPLORATION:
                score /= 10.0
            elif ID != EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_MILITARY:
                score /= 2.0
        if topscore < score:
            topscore = score  # don't really need topscore nor sorting with current handling
        print " Score: %4d -- %s " % (score, EnumsAI.AIPriorityNames[ID])
        if ID != EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_BUILDINGS:
            if(ID == EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_COLONISATION
               and tot_colony_fleets < max_colony_fleets
               and colony_build_choices is not None
               and len(colony_build_choices) > 0
               ):
                filtered_priorities[ID] = score
            elif ID == EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_OUTPOST and tot_outpost_fleets < max_outpost_fleets:
                filtered_priorities[ID] = score
            elif ID not in [EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_OUTPOST,
                            EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_COLONISATION]:
                filtered_priorities[ID] = score
    if filtered_priorities == {}:
        print "No non-building-production priorities with nonzero score, setting to default: Military"
        filtered_priorities[EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_MILITARY] = 1
    if topscore <= 100:
        scaling_power = 1.0
    else:
        scaling_power = math.log(100) / math.log(topscore)
    for pty in filtered_priorities:
        filtered_priorities[pty] **= scaling_power

    available_pp = dict([(tuple(el.key()), el.data()) for el in
                        empire.planetsWithAvailablePP])  # keys are sets of ints; data is doubles
    allocated_pp = dict([(tuple(el.key()), el.data()) for el in
                        empire.planetsWithAllocatedPP])  # keys are sets of ints; data is doubles
    planets_with_wasted_pp = set([tuple(pidset) for pidset in empire.planetsWithWastedPP])
    print "avail_pp (<systems> : pp):"
    for pSet in available_pp:
        print "\t%s\t%.2f" % (
            ppstring(PlanetUtilsAI.sys_name_ids(set(PlanetUtilsAI.get_systems(pSet)))), available_pp[pSet])
    print "\nallocated_pp (<systems> : pp):"
    for pSet in allocated_pp:
        print "\t%s\t%.2f" % (
            ppstring(PlanetUtilsAI.sys_name_ids(set(PlanetUtilsAI.get_systems(pSet)))), allocated_pp[pSet])

    print "\n\nBuilding Ships in system groups with remaining PP:"
    for pSet in planets_with_wasted_pp:
        total_pp = available_pp.get(pSet, 0)
        avail_pp = total_pp - allocated_pp.get(pSet, 0)
        if avail_pp <= 0.01:
            continue
        print "%.2f PP remaining in system group: %s" % (
            avail_pp, ppstring(PlanetUtilsAI.sys_name_ids(set(PlanetUtilsAI.get_systems(pSet)))))
        print "\t owned planets in this group are:"
        print "\t %s" % (ppstring(PlanetUtilsAI.planet_name_ids(pSet)))
        best_design_id, best_design, build_choices = get_best_ship_info(
            EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_COLONISATION, list(pSet))
        species_map = {}
        for loc in (build_choices or []):
            this_spec = universe.getPlanet(loc).speciesName
            species_map.setdefault(this_spec, []).append(loc)
        colony_build_choices = []
        for pid, (score, this_spec) in foAI.foAIstate.colonisablePlanetIDs.items():
            colony_build_choices.extend(
                int(math.ceil(score)) * [pid2 for pid2 in species_map.get(this_spec, []) if pid2 in pSet])

        local_priorities = {}
        local_priorities.update(filtered_priorities)
        best_ships = {}
        military_build_choices = get_best_ship_ratings(list(pSet))
        for priority in list(local_priorities):
            if priority == EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_MILITARY:
                if not military_build_choices:
                    del local_priorities[priority]
                    continue
                top = military_build_choices[0]
                best_design_id, best_design, build_choices = top[2], top[3], [top[1]]
                # score = ColonisationAI.pilotRatings.get(pid, 0)
                # if bestScore < ColonisationAI.curMidPilotRating:
            else:
                best_design_id, best_design, build_choices = get_best_ship_info(priority, list(pSet))
            if best_design is None:
                del local_priorities[priority]  # must be missing a shipyard -- TODO build a shipyard if necessary
                continue
            best_ships[priority] = [best_design_id, best_design, build_choices]
            print "best_ships[%s] = %s \t locs are %s from %s" % (
                EnumsAI.AIPriorityNames[priority], best_design.name(False), build_choices, pSet)

        if len(local_priorities) == 0:
            print "Alert!! need shipyards in systemSet ", ppstring(
                PlanetUtilsAI.sys_name_ids(set(PlanetUtilsAI.get_systems(sorted(pSet)))))
        priority_choices = []
        for priority in local_priorities:
            priority_choices.extend(int(local_priorities[priority]) * [priority])

        loop_count = 0
        while (avail_pp > 0) and (loop_count < max(100, current_turn)) and (priority_choices != []):
            # make sure don't get stuck in some nonbreaking loop like if all shipyards captured
            loop_count += 1
            print "Beginning build enqueue loop %d; %.1f PP available" % (loop_count, avail_pp)
            this_priority = random.choice(priority_choices)
            print "selected priority: ", EnumsAI.AIPriorityNames[this_priority]
            making_colony_ship = False
            making_outpost_ship = False
            if this_priority == EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_COLONISATION:
                if tot_colony_fleets >= max_colony_fleets:
                    print "Already sufficient colony ships in queue, trying next priority choice"
                    print
                    for i in range(len(priority_choices) - 1, -1, -1):
                        if priority_choices[i] == EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_COLONISATION:
                            del priority_choices[i]
                    continue
                elif colony_build_choices is None or len(colony_build_choices) == 0:
                    for i in range(len(priority_choices) - 1, -1, -1):
                        if priority_choices[i] == EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_COLONISATION:
                            del priority_choices[i]
                    continue
                else:
                    making_colony_ship = True
            if this_priority == EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_OUTPOST:
                if tot_outpost_fleets >= max_outpost_fleets:
                    print "Already sufficient outpost ships in queue, trying next priority choice"
                    print
                    for i in range(len(priority_choices) - 1, -1, -1):
                        if priority_choices[i] == EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_OUTPOST:
                            del priority_choices[i]
                    continue
                else:
                    making_outpost_ship = True
            best_design_id, best_design, build_choices = best_ships[this_priority]
            if making_colony_ship:
                loc = random.choice(colony_build_choices)
                best_design_id, best_design, build_choices = get_best_ship_info(
                    EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_COLONISATION, loc)
            elif this_priority == EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_MILITARY:
                selector = random.random()
                choice = military_build_choices[0]  # military_build_choices can't be empty due to earlier check
                for choice in military_build_choices:
                    if choice[0] >= selector:
                        break
                loc, best_design_id, best_design = choice[1:4]
                if best_design is None:
                    print "Error: problem with military_build_choices; with selector (%s) chose loc (%s), best_design_id (%s), best_design (None) from military_build_choices: %s" % (
                        selector, loc, best_design_id, military_build_choices)
                    continue
                    # print "Mil ship choices ", loc, best_design_id, " from ", choice
            else:
                loc = random.choice(build_choices)

            num_ships = 1
            per_turn_cost = (float(best_design.productionCost(empire.empireID, loc)) / best_design.productionTime(empire.empireID, loc))
            if this_priority == EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_MILITARY:
                this_rating = ColonisationAI.pilot_ratings.get(loc, 0)
                rating_ratio = float(this_rating) / ColonisationAI.get_best_pilot_rating()
                if rating_ratio < 0.1:
                    loc_planet = universe.getPlanet(loc)
                    if loc_planet:
                        pname = loc_planet.name
                        this_rating = ColonisationAI.rate_planetary_piloting(loc)
                        rating_ratio = float(this_rating) / ColonisationAI.get_best_pilot_rating()
                        qualifier = ["", "suboptimal"][rating_ratio < 1.0]
                        print "Building mil ship at loc %d (%s) with %s pilot Rating: %.1f; ratio to empire best is %.1f" % (
                            loc, pname, qualifier, this_rating, rating_ratio)
                while total_pp > 40 * per_turn_cost:
                    num_ships *= 2
                    per_turn_cost *= 2
            for i in xrange(0, num_ships):
                retval = foAI.foAIstate.production_queue_manager.enqueue_item(SHIP, best_design_id, loc,
                                                                              PRIORITY_SHIP_MIL)
            if retval != 0:
                print "adding %d new ship(s) at location %s to production queue: %s; per turn production cost %.1f" % (
                    num_ships, ppstring(PlanetUtilsAI.planet_name_ids([loc])), best_design.name(True), per_turn_cost)
                print
                avail_pp -= per_turn_cost
                if making_colony_ship:
                    tot_colony_fleets += num_ships
                    continue
                if making_outpost_ship:
                    tot_outpost_fleets += num_ships
                    continue
                if total_pp > 10 * per_turn_cost:
                    leading_block_pp = 0
                    for elem in [production_queue[elemi] for elemi in range(0, min(4, production_queue.size))]:
                        cost, prod_time = empire.productionCostAndTime(elem)
                        leading_block_pp += elem.blocksize * cost / prod_time
        print
    fo.updateProductionQueue()


def build_ship_facilities(bld_name, best_pilot_facilities, top_locs=None):
    """Build specified ship facility at suitable locations if criteria are met.

    :param bld_name: Ship facility to be built
    :type bld_name: str
    :param best_pilot_facilities: As specified by ColonisationAI.facilities_by_species_grade
    :type best_pilot_facilities: dict
    :param top_locs: prefered locations for building
    :type top_locs: list | None
    """
    # first, check early return conditions
    empire = fo.getEmpire()
    if not empire.buildingTypeAvailable(bld_name):
        return

    min_aggr, prereq_bldg, this_cost, turns_to_finish = AIDependencies.SHIP_FACILITIES.get(bld_name, (None, '', -1, -1))
    if min_aggr is None or min_aggr > foAI.foAIstate.aggression:
        return

    if top_locs is None:
        top_locs = []
    bld_type = fo.getBuildingType(bld_name)
    total_pp = empire.productionPoints
    universe = fo.getUniverse()
    queued_bld_locs = [element.locationID for element in empire.productionQueue if element.name == bld_name]

    if bld_name in AIDependencies.SYSTEM_SHIP_FACILITIES:
        current_locs = ColonisationAI.system_facilities.get(bld_name, {}).get('systems', set())
        current_coverage = current_locs.union(universe.getPlanet(planet_id).systemID for planet_id in queued_bld_locs)
        open_systems = set(universe.getPlanet(pid).systemID
                           for pid in best_pilot_facilities.get("BLD_SHIPYARD_BASE", [])).difference(current_coverage)
        try_systems = open_systems.intersection(ColonisationAI.system_facilities.get(
            prereq_bldg, {}).get('systems', [])) if prereq_bldg else open_systems
        try_locs = set(pid for sys_id in try_systems for pid in AIstate.colonizedSystems.get(sys_id, []))
    else:
        current_locs = best_pilot_facilities.get(bld_name, [])
        try_locs = set(best_pilot_facilities.get(prereq_bldg, [])).difference(
            queued_bld_locs, current_locs)
    print "Considering constructing a %s, have %d already built and %d queued" % (
        bld_name, len(current_locs), len(queued_bld_locs))
    max_under_construction = max(1, (turns_to_finish * total_pp) // (5 * this_cost))
    max_total = max(1, (turns_to_finish * total_pp) // (2 * this_cost))
    print "Allowances: max total: %d, max under construction: %d" % (max_total, max_under_construction)
    if len(current_locs) >= max_total:
        return
    valid_locs = (list(loc for loc in try_locs.intersection(top_locs) if bld_type.canBeProduced(empire.empireID, loc)) +
                  list(loc for loc in try_locs.difference(top_locs) if bld_type.canBeProduced(empire.empireID, loc)))
    print "Have %d potential locations: %s" % (len(valid_locs), map(universe.getPlanet, valid_locs))
    # TODO: rank by defense ability, etc.
    num_queued = len(queued_bld_locs)
    already_covered = []  # just those covered on this turn
    while valid_locs:
        if num_queued >= max_under_construction:
            break
        pid = valid_locs.pop()
        if pid in already_covered:
            continue
        res = foAI.foAIstate.production_queue_manager.enqueue_item(BUILDING, bld_name, pid, PRIORITY_BUILDING_LOW)
        if res:
            num_queued += 1
            already_covered.extend(AIstate.colonizedSystems[universe.getPlanet(pid).systemID])


class ProductionQueueManager(object):
    """This class handles the priority management of the production queue.

    This class should be instanced only once and only by foAI.foAIstate in order to provide save-load functionality!

    It is absolutely mandatory that any enqueuing and dequeuing regarding the production queue
    is handled by this class. I.e., DO NOT CALL the following functions directy:
        -fo.issueEnqueueBuildingProductionOrder
        -fo.issueEnqueueShipProductionOrder
        -fo.issueRequeueProductionOrder
        -fo.issueDequeueProductionOrder
    Instead, use the dedicated member functions of this class.

    If extending the interface, make sure to always update self._production_queue. Make sure that its content
    is consistent with the ingame production queue (i.e. C++ part of the game) at all times - both item and order.
    """

    def __init__(self):
        self._production_queue = []  # sorted list of (current_priority, base_priority, item_type, item, loc) tuples
        self._items_finished_last_turn = []  # sorted list of (current_priority, base_priority, item_type, item, loc) tuples
        self._items_lost_last_turn = []  # sorted list of (current_priority, base_priority, item_type, item, loc) tuples
        self._conquered_items_last_turn = []
        self._number_of_invalid_priorities = 0
        self._last_update = -1

    def update_for_new_turn(self):
        """Check for completed items and adjust priorities according to production progress.

        :return:
        """
        cur_turn = fo.currentTurn()
        if self._last_update == cur_turn:
            return
        self._last_update = cur_turn
        print "Checking Production queues:"
        legend = " # (priority, item_type (1=BUILDING, 2=SHIP), item, loc)"
        print "AI-priority-queue last turn: ", self._production_queue, legend

        ingame_production_queue = fo.getEmpire().productionQueue
        ingame_queue_list = []
        for element in ingame_production_queue:
            ingame_queue_list.append(self.get_name_of_production_queue_element(element))
        print "Production queue this turn: ", ingame_queue_list

        lost_planets, gained_planets = _get_change_of_planets()
        # Loop over all elements in the ingame_production_queue and try to find a match in self._production_queue.
        # As order is preserverd between turns, if items do not match, the corresponding item in self._production_queue
        # must have been completed last turn. In that case, remove the entry from our list.
        self._items_finished_last_turn = []
        self._items_lost_last_turn = []
        self._conquered_items_last_turn = []
        for i, element in enumerate(ingame_production_queue):
            item = self.get_name_of_production_queue_element(element)
            while True:
                try:
                    if element.locationID in gained_planets:
                        cur_priority = 0.0
                        base_priority = 0.0
                        item_type = (EnumsAI.AIEmpireProductionTypes.BT_BUILDING
                                     if element.buildType == EnumsAI.AIEmpireProductionTypes.BT_SHIP
                                     else EnumsAI.AIEmpireProductionTypes.BT_SHIP)
                        this_item = self.get_name_of_production_queue_element(element)
                        loc = element.locationID
                        self._production_queue.insert(i, (cur_priority, base_priority, item_type, this_item, loc))
                        self._conquered_items_last_turn.append((cur_priority, base_priority, item_type, this_item, loc))
                        continue  # sort later on TODO: adjust priority based on needs, dequeue etc...
                    (cur_priority, base_priority, item_type, this_item, loc) = self._production_queue[i]
                    if this_item == item and loc == element.locationID:  # item not finished yet, keep in list
                        break
                    elif loc in lost_planets:  # we lost the planet, thus item is no longer in queue, we can remove it.
                        self._items_lost_last_turn.append(self._production_queue.pop(i))
                    else:  # item was finished in last turn, remove from our queue.
                        self._items_finished_last_turn.append(self._production_queue.pop(i))
                except Exception as e:
                    print element.name, element.locationID
                    print self._items_finished_last_turn
                    print_error(e)
                    break

        # some items in our list may have not been matched yet with items in the ingame-production queue
        for remaining_item in self._production_queue[len(ingame_production_queue):]:
            (cur_priority, base_priority, item_type, this_item, loc) = remaining_item
            if loc in lost_planets:
                self._items_lost_last_turn.append(remaining_item)
            else:  # not in queue anymore, planet still in our hands... item must be finished!
                self._items_finished_last_turn.append(remaining_item)
        del self._production_queue[len(ingame_production_queue):]

        print "Items that were finished in last turn: ", self._items_finished_last_turn
        print "Items that we were building on planets we lost during last turn: ", self._items_lost_last_turn
        print "Items that were already queued on planets we conquered last turn: ", self._conquered_items_last_turn

        # update priorities according to production progress and adjust the queue accordingly.
        # We want to complete started / nearly finished projects first thus scale it with ratio of progress.
        old_queue = list(self._production_queue)
        for tup in old_queue:
            idx = bisect.bisect_left(self._production_queue, tup)  # as we sort self._production_queue, need to search!
            (old_priority, base_priority, item_type, this_item, loc) = self._production_queue.pop(idx)
            ingame_production_queue = fo.getEmpire().productionQueue  # make sure to get updated copy
            element = ingame_production_queue[idx]
            total_cost, total_turns = fo.getEmpire().productionCostAndTime(element)
            new_priority = float(base_priority) * (1 - float(element.progress) / float(total_cost))
            new_entry = (new_priority, base_priority, item_type, this_item, loc)
            new_index = bisect.bisect_left(self._production_queue, new_entry)
            self._production_queue.insert(new_index, new_entry)
            if new_index != idx:  # need to move item
                fo.issueRequeueProductionOrder(idx, new_index)
        print "New AI-priority-queue: ", self._production_queue, legend

    def get_name_of_production_queue_element(self, elem):
        """
        :param elem: element of production queue
        :return: name of Building or id of ShipDesign of the element"""
        return elem.designID if elem.buildType == EnumsAI.AIEmpireProductionTypes.BT_SHIP else elem.name

    def enqueue_item(self, item_type, item, loc, priority=PRIORITY_DEFAULT):
        """Enqueue item into production queue.

        :param item_type: type of the item to queue: ship or building
        :type item_type: EnumsAI.AIEmpireProductionTypes
        :param item: Building name or ShipDesign id
        :type item: str or int
        :param priority: production priority
        :type priority: float
        :param loc: Planet id
        :type loc: int
        :return: True if successfully enqueued, otherwise False
        :rtype: bool
        """
        # first check which type of item we want to queue to find the right C++ function to call

        print "Trying to enqueue %s at %d" % (item, loc)
        if item_type == EnumsAI.AIEmpireProductionTypes.BT_BUILDING:
            production_order = fo.issueEnqueueBuildingProductionOrder
        elif item_type == EnumsAI.AIEmpireProductionTypes.BT_SHIP:
            production_order = fo.issueEnqueueShipProductionOrder
        else:
            print_error("Tried to queue invalid item to production queue.",
                        location="ProductionQueueManager.enqueue_item(%s, %s, %s, %s)" % (
                            priority, item_type, item, loc),
                        trace=False)
            return False

        # call C++ production order
        try:
            res = production_order(item, loc)
        except Exception as e:
            print_error(e)
            return False
        if not res:
            print_error("Can not queue item to production queue.",
                        location="ProductionQueueManager.enqueue_item(%s, %s, %s, %s)" % (
                            priority, item_type, item, loc),
                        trace=False)
            return False

        # Only now that we are sure to have enqueued the item, we keep track of it in our priority-sorted queue.
        entry = (priority, priority, item_type, item, loc)
        idx = bisect.bisect(self._production_queue, entry)
        self._production_queue.insert(idx, entry)
        if idx == len(self._production_queue) - 1:  # item does not need to be moved in queue
            print "After enqueuing ", item, ":"
            print "self._production_queue: ", [tup[3] for tup in self._production_queue]
            print "real productionQueue: ", [self.get_name_of_production_queue_element(elem) for elem in fo.getEmpire().productionQueue]
            return True

        # move item in production queue according to its priority.
        try:
            print "Trying to move item to correct position..."
            res = fo.issueRequeueProductionOrder(fo.getEmpire().productionQueue.size - 1, idx)  # move to right position
        except Exception as e:
            print_error(e)
            self.__handle_error_on_requeue(self._production_queue.pop(idx))
        if not res:
            print_error("Can not change position of item in production queue.")
            self.__handle_error_on_requeue(self._production_queue.pop(idx))
        print "After enqueuing ", item, ":"
        print "self._production_queue: ", [tup[3] for tup in self._production_queue]
        print "real productionQueue: ", [self.get_name_of_production_queue_element(elem) for elem in fo.getEmpire().productionQueue]
        return True

    def dequeue_item_by_index(self, index):
        """Dequeues item at specified index.

        :param index: Index of the production queue.
        :type index: int
        :return: Success
        :rtype: bool
        """
        try:
            res = fo.issueDequeueProductionOrder(index)
        except Exception as e:
            print_error(e)
            return False
        if res:
            del self._production_queue[index]
        return res

    def __handle_error_on_requeue(self, item_tuple):
        """Handle the case that freshly enqueued item could not be moved into right position in queue.

        :param item_tuple:
        :return:
        """
        new_priority = PRIORITY_INVALID + self._number_of_invalid_priorities
        new_entry = tuple(
            [new_priority, new_priority, item_tuple[2:]])  # give invalid priority marking it is at the end of the queue
        self._number_of_invalid_priorities += 1
        self._production_queue.append(new_entry)

    def get_all_queued_buildings(self):
        queued_bldgs = {}
        for (cur_priority, base_priority, item_type, this_item, loc) in self._production_queue:
            if item_type == EnumsAI.AIEmpireProductionTypes.BT_BUILDING:
                queued_bldgs.setdefault(this_item, []).append(loc)
        return queued_bldgs


def _get_change_of_planets():
    all_planets = fo.getUniverse().planetIDs
    currently_owned_planets = set(PlanetUtilsAI.get_owned_planets_by_empire(all_planets))
    old_outposts = AIstate.outpostIDs
    old_popctrs = AIstate.popCtrIDs
    old_owned_planets = set(old_outposts + old_popctrs)
    newly_gained_planets = currently_owned_planets - old_owned_planets
    lost_planets = old_owned_planets - currently_owned_planets
    return tuple(lost_planets), tuple(newly_gained_planets)


def _get_all_existing_buildings():
    """Return all existing buildings in the empire with locations.

    :return: Existing buildings in the empire with locations (planet_ids)
    :rtype: dict{str: int}
    """
    existing_buildings = {}  # keys are building names, entries are planet ids where building stands
    universe = fo.getUniverse()
    for pid in set(AIstate.popCtrIDs + AIstate.outpostIDs):
        planet = universe.getPlanet(pid)
        if not planet:
            sys.stderr.write('Can not find planet with id %d' % pid)
            continue
        for bld_name in [bld.buildingTypeName for bld in map(universe.getObject, planet.buildingIDs)]:
            existing_buildings.setdefault(bld_name, []).append(pid)
    return existing_buildings


def _get_system_closest_to_target(system_ids, target_system_id):
    universe = fo.getUniverse()
    distances = []
    for sys_id in system_ids:
        if sys_id != -1:  # check only valid systems
            try:
                distance = universe.jumpDistance(target_system_id, sys_id)
                distances.append((distance, sys_id))
            except Exception as e:
                print_error(e, location="ProductionAI._get_system_closest_to_target")
    shortest_distance, closest_system = sorted(distances)[0] if distances else (9999, -1)  # -1: invalid system_id
    return closest_system, shortest_distance


def _count_empire_foci():
    """Count current foci settings in empire.
    :rtype : tuple(int, int)
    :return: number_of_production_foci, number_of_research_foci
    """
    universe = fo.getUniverse()
    n_production = 0
    n_research = 0
    for planet_id in AIstate.popCtrIDs:
        planet = universe.getPlanet(planet_id)
        if not planet:
            continue
        focus = planet.focus
        if focus == EnumsAI.AIFocusType.FOCUS_INDUSTRY:
            n_production += 1
        elif focus == EnumsAI.AIFocusType.FOCUS_RESEARCH:
            n_research += 1
    return n_production, n_research
