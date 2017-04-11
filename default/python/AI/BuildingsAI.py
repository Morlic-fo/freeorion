import sys

import freeOrionAIInterface as fo
import FreeOrionAI as foAI
import AIDependencies
import AIstate
import ColonisationAI
import PlanetUtilsAI
import ProductionAI
from turn_state import state
from character.character_module import Aggression
from freeorion_tools import tech_is_complete, print_error
from ProductionQueueAI import BUILDING, ProductionPriority as Priority
from EnumsAI import FocusType


WHITESPACE = 4*" "
ARB_LARGE_NUMBER = 1e4


class BuildingCache(object):
    """Caches stuff important to buildings..."""
    existing_buildings = None
    queued_buildings = None
    n_production_focus = None
    n_research_focus = None
    total_production = None

    def __init__(self):
        pass  # cant update before imports complete

    def update(self):
        """Update the cache."""
        self.existing_buildings = get_all_existing_buildings()
        self.queued_buildings = foAI.foAIstate.production_queue_manager.get_all_queued_buildings()
        self.n_production_focus, self.n_research_focus = _count_empire_foci()
        self.total_production = fo.getEmpire().productionPoints


bld_cache = BuildingCache()


class BuildingManager(object):
    """Manages the construction of buildings."""
    name = ""
    production_cost = 99999
    production_time = 99999
    priority = Priority.building_base
    minimum_aggression = fo.aggression.beginner

    def __init__(self):
        self.building = fo.getBuildingType(self.name)
        if not self.building:
            print "Specified invalid building!"
        else:
            empire_id = fo.empireID()
            capital_id = PlanetUtilsAI.get_capital()
            self.production_cost = self.building.productionCost(empire_id, capital_id)
            self.production_time = self.building.productionTime(empire_id, capital_id)

    def make_building_decision(self):
        """Make a decision if we want to build the building and if so, enqueue it.

         :return: True if we enqueued it, otherwise False
         :rtype: bool
        """
        print "Deciding if we want to build a %s..." % self.name
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
        print WHITESPACE + "Checking aggression level: Need %d, have %d..." % (self.minimum_aggression,
                                                                               foAI.foAIstate.aggression),
        if foAI.foAIstate.aggression < self.minimum_aggression:
            print "Failed! Do not enqueue building!"
            return False
        print "Passed!"

        print WHITESPACE + "Checking if we need another one..."
        if not self._need_another_one():
            print WHITESPACE + "Failed! Do not enqueue building!"
            return False
        print WHITESPACE + "Passed!"

        print WHITESPACE + "Checking suitable locations...",
        if not self._suitable_locations():
            print "Failed! No suitable locations. Do not enqueue building!"
            return False
        print "Passed!"
        # passed all tests
        return True


class GenericUniqueBuilding(BuildingManager):

    def _suitable_locations(self):
        return list(AIstate.outpostIDs + AIstate.popCtrIDs)

    def _enqueue_locations(self):
        capitol_id = PlanetUtilsAI.get_capital()
        if capitol_id and capitol_id != -1:
            return [capitol_id]
        else:
            return self._suitable_locations()[0]

    def _need_another_one(self):
        # default: Build only once per empire
        if self.name in bld_cache.existing_buildings:
            print 2*WHITESPACE + "We already have existing buildings of this type at %s" % (
                bld_cache.existing_buildings[self.name])
            return False
        if self.name in bld_cache.queued_buildings:
            print 2*WHITESPACE + "We already have enqueued buildings of this type at %s" % (
                bld_cache.queued_buildings[self.name])
            return False
        print 2*WHITESPACE + "We do not have a building of this type yet!"
        return True


class GenomeBankManager(GenericUniqueBuilding):
    name = "BLD_GENOME_BANK"
    priority = Priority.building_low
    minimum_aggression = fo.aggression.typical

    def _should_be_built(self):
        if not GenericUniqueBuilding._should_be_built(self):
            return False
        cost_per_turn = float(self.production_cost) / self.production_time
        if cost_per_turn > bld_cache.total_production/50:
            print "FAILED: Cost per turn (%.1f) is higher than two percent of production output. Do not build!" % (
                cost_per_turn)
            return False
        print "Passed. Empire production output is large enough to fit this in."
        return True


class ImperialPalaceManager(GenericUniqueBuilding):
    """Handles the building of imperial palaces.

    Rules: Build one, if none existing yet.
    """
    name = "BLD_IMPERIAL_PALACE"
    priority = Priority.building_high
    minimum_aggression = fo.aggression.beginner

    def _suitable_locations(self):
        return list(AIstate.popCtrIDs)


class NeutroniumSynthManager(GenericUniqueBuilding):
    """Handles the building of the neutronium synthetizer.

    Rule: If we do not have one yet, build one preferable at the capital.
    """
    name = "BLD_NEUTRONIUM_SYNTH"
    priority = Priority.building_low
    minimum_aggression = fo.aggression.maniacal

    def _suitable_locations(self):
        return list(AIstate.popCtrIDs)


class NeutroniumExtractorManager(GenericUniqueBuilding):
    """Handles the building of the neutronium extractor.

    Rules:
    -Valid locations are either neutron star systems or planets with neutronium synthetizer.
    -Build only if we have no valid neutronium extractor yet
    -Build location is closest suitable system to capital (random planet)
    """
    name = "BLD_NEUTRONIUM_EXTRACTOR"
    minimum_aggression = fo.aggression.maniacal
    priority = Priority.building_low

    def _suitable_locations(self):
        planets_with_neutron = set(AIstate.outpostIDs + AIstate.popCtrIDs).intersection(
            PlanetUtilsAI.get_planets_in__systems_ids(AIstate.empireStars.get(fo.starType.neutron, [])))
        planets_with_neutron.update(bld_cache.existing_buildings.get("BLD_NEUTRONIUM_SYNTH", []))
        if not planets_with_neutron:
            return []
        return PlanetUtilsAI.get_systems(planets_with_neutron)

    def _enqueue_locations(self):
        capitol_sys_id = PlanetUtilsAI.get_capital_sys_id()
        if capitol_sys_id == -1:
            use_sys = self._suitable_locations()[0]
        else:
            use_sys, _ = _get_system_closest_to_target(self._suitable_locations(), capitol_sys_id)
        if use_sys and use_sys != -1:
            use_loc = AIstate.colonizedSystems[use_sys][0]
            return [use_loc]
        return []

    def _need_another_one(self):
        planets_with_neutron = set(AIstate.outpostIDs + AIstate.popCtrIDs).intersection(
            PlanetUtilsAI.get_planets_in__systems_ids(AIstate.empireStars.get(fo.starType.neutron, [])))
        planets_with_neutron.update(bld_cache.existing_buildings.get("BLD_NEUTRONIUM_SYNTH", []))

        valid_existing_locs = planets_with_neutron.intersection(bld_cache.existing_buildings.get(self.name, []))
        if valid_existing_locs:
            print "Already have a valid building at ", valid_existing_locs
            return False

        valid_queued_locs = planets_with_neutron.intersection(bld_cache.queued_buildings.get(self.name, []))
        if valid_queued_locs:
            print "Already have a building queued at ", valid_existing_locs
            return False
        # TODO: Dequeue invalid locs

        print "We do not have a valid building yet!"
        return True


class ArtificialBlackHoleManager(BuildingManager):
    name = "BLD_ART_BLACK_HOLE"
    minimum_aggression = fo.aggression.typical
    priority = Priority.building_base

    def _suitable_locations(self):
        def get_candidate(pids):
            # TODO: Implement scnearios where we allow to kill of Phototrophics ... for the greater good!
            candidates = []
            for this_pid in pids:
                planet = universe.getPlanet(this_pid)
                if not planet:
                    continue
                for pid2 in PlanetUtilsAI.get_empire_planets_in_system(planet.systemID):
                    planet2 = universe.getPlanet(pid2)
                    if planet2 and planet2.speciesName:
                        species = fo.getSpecies(planet2.speciesName)
                        if species and "PHOTOTROPHIC" in species.tags:
                            break
                else:
                    candidates.append(this_pid)
            return candidates

        universe = fo.getUniverse()

        red_star_systems = AIstate.empireStars.get(fo.starType.red, [])
        if not red_star_systems:
            return []
        black_hole_systems = AIstate.empireStars.get(fo.starType.blackHole, [])
        red_popctrs = sorted([(ColonisationAI.pilot_ratings.get(pid, 0), pid) for pid in AIstate.popCtrIDs
                              if PlanetUtilsAI.get_systems([pid])[0] in red_star_systems],
                             reverse=True)
        bh_popctrs = sorted([(ColonisationAI.pilot_ratings.get(pid, 0), pid) for pid in AIstate.popCtrIDs
                             if PlanetUtilsAI.get_systems([pid])[0] in black_hole_systems],
                            reverse=True)
        red_pilots = [pid for rating, pid in red_popctrs if rating == state.best_pilot_rating]
        bh_pilots = [pid for rating, pid in bh_popctrs if rating == state.best_pilot_rating]
        if not bh_pilots and red_pilots and "SH_SOLAR" in fo.getEmpire().availableShipHulls:  # TODO: Generalize
            candidate_locs = get_candidate(red_pilots)
            if candidate_locs:
                return candidate_locs
        if fo.getEmpire().buildingTypeAvailable("BLD_BLACK_HOLE_POW_GEN") and not black_hole_systems:
            capitol_sys_id = PlanetUtilsAI.get_capital_sys_id()
            if capitol_sys_id == -1:
                use_sys = red_star_systems[0]
            else:
                use_sys, _ = _get_system_closest_to_target(red_star_systems, capitol_sys_id)
            if use_sys and use_sys != -1:
                use_loc = AIstate.colonizedSystems[use_sys][0]
                return [use_loc]
        return []

    def _enqueue_locations(self):
        return self._suitable_locations()[0]

    def _need_another_one(self):
        existing_locs = bld_cache.existing_buildings.get(self.name, [])
        if set(existing_locs).intersection(self._suitable_locations()):
            print 2*WHITESPACE + "Already got one at %s (Takes 1 turn to take effect...)" % (
                bld_cache.existing_buildings.get(self.name))
            return False

        queued_locs = bld_cache.queued_buildings.get(self.name, [])
        if set(queued_locs).intersection(self._suitable_locations()):
            print 2*WHITESPACE + "Already got one enqueued at ", bld_cache.queued_buildings.get(self.name)
            return False
        # TODO: Dequeue invalid locations
        print "Have no building of this type yet."
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
        if self.name in bld_cache.existing_buildings:
            print 2*WHITESPACE + "We already have existing buildings of this type at %s" % (
                bld_cache.existing_buildings[self.name])
            return False
        if self.name in bld_cache.queued_buildings:
            print 2*WHITESPACE + "We already have enqueued buildings of this type at %s" % (
                bld_cache.queued_buildings[self.name])
            return False
        print 2*WHITESPACE + "We do not have a building of this type yet!"
        return True

    def _should_be_built(self):
        if not BuildingManager._should_be_built(self):  # aggression level, need for another one, have locations...
            return False
        print WHITESPACE + "Checking if investment is worth it..."
        cost_per_turn = float(self.production_cost)/self.production_time
        turns_till_payoff = self._estimated_time_to_payoff()
        print 2*WHITESPACE + "Empire PP: %.1f" % bld_cache.total_production
        print 2*WHITESPACE + "Production cost: %d over %d turns (%.2f pp/turn)" % (
            self.production_cost, self.production_time, cost_per_turn)
        print 2*WHITESPACE + "Estimated turns till pay off: %.1f" % turns_till_payoff
        if self.production_cost > 10*bld_cache.total_production:
            print WHITESPACE + "Failed: Production cost is more than 10 times the empire production. Do not build!"
            return False
        if turns_till_payoff < 10 and cost_per_turn < 2*bld_cache.total_production:
            print WHITESPACE + ("Passed: Building pays off in less than 10 turns"
                                " and cost per turn is less than twice the empire's production.")
            return True
        if turns_till_payoff < 20 and cost_per_turn < bld_cache.total_production:
            print WHITESPACE + ("Passed: Building pays off in less than 20 turns "
                                "and cost per turn is less than empire's production.")
            return True
        if self._estimated_time_to_payoff() < 50 and cost_per_turn < .1*bld_cache.total_production:
            print WHITESPACE + ("Passed: Building pays off in less than 50 turns"
                                " and cost per turn is less than ten percent of empire's production.")
            return True
        print WHITESPACE + "Failed! Building pays off too late for current empire production output. Do not build!"
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
            number_of_pop_ctrs = bld_cache.n_research_focus
            relevant_population = ColonisationAI.empire_status['researchers']
        else:
            number_of_pop_ctrs = len(AIstate.popCtrIDs)
            relevant_population = fo.getEmpire().population()
        return number_of_pop_ctrs*self._flat_research_bonus() + relevant_population*self._research_per_pop()

    def _total_production(self):
        if self.needs_production_focus:
            number_of_pop_ctrs = bld_cache.n_production_focus
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
        total_pp = self._total_production()
        total_rp = self._total_research()
        total_economy_points = total_pp + total_rp*self.RP_TO_PP_CONVERSION_FACTOR
        print 2*WHITESPACE + "Projected boost of economy: %.1f PP, %.1f RP (weighted total of %.1f)" % (total_pp, total_rp, total_economy_points)
        return float(self.production_cost) / max(total_economy_points, 1e-12)


class AutoHistoryAnalyzerManager(EconomyBoostBuildingManager):
    name = "BLD_AUTO_HISTORY_ANALYSER"
    priority = Priority.building_high
    minimum_aggression = fo.aggression.typical

    def _flat_research_bonus(self):
        return 5.0

    def _suitable_locations(self):
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
                                                                     (ARB_LARGE_NUMBER, ARB_LARGE_NUMBER,
                                                                      ARB_LARGE_NUMBER))
        # If we can colonize good planets instead, do not build this.
        num_colony_targets = 0
        for pid in ColonisationAI.all_colony_opportunities:
            try:
                best_species_score = ColonisationAI.all_colony_opportunities[pid][0][0]
            except IndexError:
                continue
            if best_species_score > 500:
                num_colony_targets += 1

        num_covered = (ProductionAI.get_number_of_existing_outpost_and_colony_ships() +
                       ProductionAI.get_number_of_queued_outpost_and_colony_ships())
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
        # TODO should be handled in ProductionQueueAI
        queued_locs = {e.locationID for e in empire.productionQueue if
                       e.buildType == BUILDING and
                       e.name == history_analyser}

        possible_locations -= queued_locs
        chosen_locations = []
        for i in range(min(max_enqueued, len(possible_locations))):
            chosen_locations.append(possible_locations.pop())
        return chosen_locations

    def _enqueue_locations(self):
        return self._suitable_locations() or None

    def _need_another_one(self):
        return True  # as long as we have suitable locations...


class IndustrialCenterManager(EconomyBoostBuildingManager):
    """Handles building decisions for the industrial center."""
    name = "BLD_INDUSTRY_CENTER"
    needs_production_focus = True
    priority = Priority.building_high
    minimum_aggression = fo.aggression.beginner

    def _production_per_pop(self):
        if tech_is_complete("PRO_INDUSTRY_CENTER_III"):
            return 3.0 * AIDependencies.INDUSTRY_PER_POP
        elif tech_is_complete("PRO_INDUSTRY_CENTER_II"):
            return 2.0 * AIDependencies.INDUSTRY_PER_POP
        elif tech_is_complete("PRO_INDUSTRY_CENTER_I"):
            return 1.0 * AIDependencies.INDUSTRY_PER_POP
        else:
            return 0.0


class VoidEnclaveManager(EconomyBoostBuildingManager):
    name = "BLD_ENCLAVE_VOID"
    needs_research_focus = True
    priority = Priority.building_high
    minimum_aggression = fo.aggression.beginner

    def _research_per_pop(self):
        return 3.75 * AIDependencies.RESEARCH_PER_POP


class GasGiantGeneratorManager(EconomyBoostBuildingManager):
    name = "BLD_GAS_GIANT_GEN"
    needs_production_focus = True
    priority = Priority.building_high
    minimum_aggression = fo.aggression.beginner

    def __init__(self):
        EconomyBoostBuildingManager.__init__(self)
        self.current_loc = -1
        self.suitable_locs = []
        self.final_locs = []

    def _suitable_locations(self):
        if self.suitable_locs:
            return self.suitable_locs
        universe = fo.getUniverse()
        planet_list = []
        covered_systems = set()
        covered_systems.update(PlanetUtilsAI.get_systems(bld_cache.existing_buildings.get(self.name, [])
                                                         + bld_cache.queued_buildings.get(self.name, [])))
        for planet_id in (AIstate.popCtrIDs + AIstate.outpostIDs):
            planet = universe.getPlanet(planet_id)
            if not planet:
                continue
            if planet.size == fo.planetSize.gasGiant:
                sys_id = PlanetUtilsAI.get_systems([planet_id])[0]
                if sys_id in covered_systems:
                    continue
                planet_list.append(planet_id)
                covered_systems.add(sys_id)
        self.suitable_locs = planet_list
        return self.suitable_locs

    def _total_production(self):
        universe = fo.getUniverse()
        current_sys = PlanetUtilsAI.get_systems([self.current_loc])[0]
        empire_target_planets = PlanetUtilsAI.get_empire_planets_in_system(current_sys)
        num_targets = 0
        for pid in empire_target_planets:
                planet = universe.getPlanet(pid)
                if not planet:
                    continue
                species_name = planet.speciesName
                if not species_name:
                    continue
                species = fo.getSpecies(species_name)
                if not species:
                    continue
                if FocusType.FOCUS_INDUSTRY in species.foci:
                    num_targets += 1
        return num_targets * 10

    def _need_another_one(self):
        print 2*WHITESPACE + "No global limit on gas giant generator count!"
        return True

    def _enqueue_locations(self):
        return self.final_locs

    def _should_be_built(self):
        should_build = False
        if not self._suitable_locations():
            print "Failed! No suitable location found."
            return False
        for pid in self._suitable_locations():
            print 2*" " + "Checking gas giant with ID %d (system %d)" % (pid, PlanetUtilsAI.get_systems([pid])[0])
            self.current_loc = pid
            if EconomyBoostBuildingManager._should_be_built(self):
                should_build = True
                self.final_locs.append(self.current_loc)
        return should_build


class BlackHolePowerGeneratorManager(EconomyBoostBuildingManager):
    name = "BLD_BLACK_HOLE_POW_GEN"
    needs_production_focus = True
    priority = Priority.building_high
    minimum_aggression = fo.aggression.cautious

    def _suitable_locations(self):
        return AIstate.empireStars.get(fo.starType.blackHole, [])

    def _production_per_pop(self):
        return 6.0 * AIDependencies.INDUSTRY_PER_POP

    def _need_another_one(self):
        existing_locs = bld_cache.existing_buildings.get(self.name, [])
        if set(PlanetUtilsAI.get_systems(existing_locs)).intersection(self._suitable_locations()):
            print 2*WHITESPACE + "Already got a building of this type at ", existing_locs
            return False

        queued_locs = bld_cache.queued_buildings.get(self.name, [])
        # TODO: Dequeue invalid locations
        if set(PlanetUtilsAI.get_systems(queued_locs)).intersection(self._suitable_locations()):
            print 2*WHITESPACE + "Already got a building of this type enqueued at ", queued_locs
            return False
        print "We do not have a building of this type yet!"
        return True

    def _enqueue_locations(self):
        capitol_sys_id = PlanetUtilsAI.get_capital_sys_id()
        if capitol_sys_id == -1:
            use_sys = self._suitable_locations()[0]
        else:
            use_sys, _ = _get_system_closest_to_target(self._suitable_locations(), capitol_sys_id)
        if use_sys and use_sys != -1:
            use_loc = AIstate.colonizedSystems[use_sys][0]
            return [use_loc]
        return []


class SolarOrbitalGeneratorManager(EconomyBoostBuildingManager):
    name = "BLD_SOL_ORB_GEN"
    needs_production_focus = True
    priority = Priority.building_high
    minimum_aggression = fo.aggression.turtle
    star_type_list = [(fo.starType.white, fo.starType.blue),
                      (fo.starType.yellow, fo.starType.orange),
                      (fo.starType.red,)]

    def __init__(self):
        EconomyBoostBuildingManager.__init__(self)
        self.currently_best_star = self._get_best_current_star()
        self.queued_best_star = self._get_best_queued_star()
        self.target_best_star = None

    def _get_best_current_star(self):
        universe = fo.getUniverse()
        existing_locs = bld_cache.existing_buildings.get(self.name, [])
        existing_systems = PlanetUtilsAI.get_systems(existing_locs)
        self.currently_best_star = 99
        for sys_id in existing_systems:
            system = universe.getSystem(sys_id)
            if not system or system == -1:
                continue
            star = system.starType
            for i, startuple in enumerate(self.star_type_list):
                if i >= self.currently_best_star:
                    break
                if star in startuple:
                    self.currently_best_star = i
                    if i == 0:
                        return self.currently_best_star  # already has best star
        return self.currently_best_star

    def _get_best_queued_star(self):
        universe = fo.getUniverse()
        queued_locs = bld_cache.queued_buildings.get(self.name, [])
        existing_systems = PlanetUtilsAI.get_systems(queued_locs)
        self.queued_best_star = 99
        for sys_id in existing_systems:
            system = universe.getSystem(sys_id)
            if not system or system == -1:
                continue
            star = system.starType
            for i, startuple in enumerate(self.star_type_list):
                if i >= self.queued_best_star:
                    break
                if star in startuple:
                    self.queued_best_star = i
                    if i == 0:
                        return self.queued_best_star  # already has best star
        return self.queued_best_star

    def _suitable_locations(self):
        locs = []
        for i, star_types in enumerate(self.star_type_list):
            if i >= self.currently_best_star or i >= self.queued_best_star:
                print WHITESPACE + "No suitable location: Already have better stars covered."
                break
            for star_type in star_types:
                locs.extend(AIstate.empireStars.get(star_type, []))
            if locs:
                self.target_best_star = i
                break
        return locs

    def _production_per_pop(self):
        prod_per_pop = {
            0: 2,   # white, blue
            1: 1,   # yellow, orange
            2: .5,  # red
        }
        target_prod_per_pop = prod_per_pop.get(self.target_best_star, 0)
        current_prod_per_pop = prod_per_pop.get(self.currently_best_star, 0)
        return target_prod_per_pop - current_prod_per_pop

    def _need_another_one(self):
        print 2*WHITESPACE + "No limitation on this building."
        return True

    def _enqueue_locations(self):
        capitol_sys_id = PlanetUtilsAI.get_capital_sys_id()
        if capitol_sys_id == -1:
            use_sys = self._suitable_locations()[0]
        else:
            use_sys, _ = _get_system_closest_to_target(self._suitable_locations(), capitol_sys_id)
        if use_sys and use_sys != -1:
            use_loc = AIstate.colonizedSystems[use_sys][0]
            return [use_loc]
        return []


def get_all_existing_buildings():  # Todo move this function to another module
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
        if focus == FocusType.FOCUS_INDUSTRY:
            n_production += 1
        elif focus == FocusType.FOCUS_RESEARCH:
            n_research += 1
    return n_production, n_research


building_manager_map = {
    # bld_name: ManagerClass
    "BLD_IMPERIAL_PALACE": ImperialPalaceManager,
    "BLD_GENOME_BANK": GenomeBankManager,
    "BLD_ART_BLACK_HOLE": ArtificialBlackHoleManager,
    "BLD_NEUTRONIUM_SYNTH": NeutroniumSynthManager,
    "BLD_NEUTRONIUM_EXTRACTOR": NeutroniumExtractorManager,
    # economy boost buildings
    "BLD_INDUSTRY_CENTER": IndustrialCenterManager,
    "BLD_ENCLAVE_VOID": VoidEnclaveManager,
    "BLD_GAS_GIANT_GEN": GasGiantGeneratorManager,
    "BLD_BLACK_HOLE_POW_GEN": BlackHolePowerGeneratorManager,
    "BLD_SOL_ORB_GEN": SolarOrbitalGeneratorManager,
}
