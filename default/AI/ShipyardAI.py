import freeOrionAIInterface as fo
import AIDependencies
import AIstate
import EnumsAI
import PlanetUtilsAI
import ProductionAI
import ShipDesignAI


class ShipyardManager(object):  # TODO: Inherit from base building class...
    """."""  # TODO: Docstring
    name = ""
    minimum_spacing = 2
    production_cost = 99999
    production_time = 99999
    prereqs = []
    system_prereqs = []
    unlocked_hulls = []
    unlocked_parts = []
    shipyard_is_system_wide = False
    inited = False
    ai_priority = EnumsAI.AIPriorityType.PRIORITY_PRODUCTION_MILITARY
    ship_designer = ShipDesignAI.MilitaryShipDesigner

    def __init__(self):
        self.building = fo.getBuildingType(self.name)
        if self.building:
            empire_id = fo.empireID()
            capital_id = PlanetUtilsAI.get_capital()
            self.production_cost = self.building.productionCost(empire_id, capital_id)
            self.production_time = self.building.productionTime(empire_id, capital_id)
        self.unlocked_hulls = list(set(self.unlocked_hulls).intersection(fo.getEmpire().availableShipHulls))
        self.unlocked_parts = list(set(self.unlocked_parts).intersection(fo.getEmpire().availableShipParts))

    def _reset_lists(self):
        if not self.inited:
            self.unlocked_hulls = []
            self.unlocked_parts = []
            self.prereqs = []
            self.system_prereqs = []
            self.inited = True

    def prereqs_available(self):
        """Check if all prerequisites are available to the empire"""
        empire = fo.getEmpire()
        for item in self.prereqs + self.system_prereqs:
            if not empire.buildingTypeAvailable(item):
                print "Prerequisite building %s for item %s is not available to empire yet." % (item, self.name)
                return False
        return True

    def get_candidate(self):
        """Get the best candidate location for the shipyard.

        :return:
        :rtype: ShipyardLocationCandidate
        """
        print "Trying to find candidates for shipyard %s" % self.name
        print "Unlocks: ", self.unlocked_hulls, self.unlocked_parts
        candidate_list = self._get_candidate_list()
        if not candidate_list:
            print "Could not get any candidates..."
            return None

        if not candidate_list[0].improvement:
            print "Shipyard is no global improvement! Minimum spacing of %d required!" % self.minimum_spacing
            print "Old length of candidate list: %d" % len(candidate_list)
            candidate_list = filter(lambda x: x.get_distance_to_yards() >= self.minimum_spacing, candidate_list)
            print "New length of candidate list: %d" % len(candidate_list)
        cheapest_candidates = self._get_cheapest_candidates(candidate_list)
        chosen_candidate = self._get_furthest_candidate(cheapest_candidates)
        return chosen_candidate

    def _get_candidate_list(self):
        _, _, old_locs, old_best_rating = ProductionAI.get_best_ship_info(self.ai_priority)
        candidates = []
        best_rating = old_best_rating
        print "Old best rating: %.2f" % old_best_rating
        improvement = False
        for pid in self._possible_locations():
            new_rating, diff = self._get_rating_improvements(pid)
            print "Checking at planet %d: New rating is %.2f (Improvement of %.2f)" % (pid, new_rating, diff)
            if diff <= 0:
                print "No improvement when building shipyard here..."
                continue
            if new_rating > best_rating:
                best_rating = new_rating
                candidates = [pid]  # delete old entries
                improvement = True
                print "This shipyard is a global improvement!"
            elif new_rating >= best_rating:
                candidates.append(pid)
                print "This location is another suitable location!"
        if not candidates:
            return None
        else:
            universe = fo.getUniverse()
            existing_yard_systems = []
            for pid in old_locs:
                existing_yard_systems.append(universe.getPlanet(pid).systemID)
            print "Existing best yard systems: ", existing_yard_systems
            candidate_list = [ShipyardLocationCandidate(pid=pid, bld_name=self.name,
                                                        prereqs=self.prereqs, system_prereqs=self.system_prereqs,
                                                        rating=best_rating, improvement=improvement,
                                                        yard_locs=existing_yard_systems,
                                                        shipyard_is_system_wide=self.shipyard_is_system_wide)
                              for pid in candidates]
        return candidate_list

    def _get_rating_improvements(self, pid):
        old_stats = self.ship_designer().optimize_design(loc=pid, consider_fleet_count=True, verbose=False)
        new_stats = self.ship_designer().optimize_design(loc=pid, consider_fleet_count=True,
                                                         additional_parts=self.unlocked_parts,
                                                         additional_hulls=self.unlocked_hulls,
                                                         verbose=False)
        old_rating = old_stats[0][0] if old_stats else ShipDesignAI.INVALID_DESIGN_RATING
        new_rating = new_stats[0][0] if new_stats else ShipDesignAI.INVALID_DESIGN_RATING
        diff = new_rating - old_rating
        return new_rating, diff

    def _possible_locations(self):
        possible_locs = self._get_shipbuilding_planets(AIstate.popCtrIDs)
        print "Possible locs: ", possible_locs
        print "AIstates... ", AIstate.popCtrIDs
        return possible_locs

    @staticmethod
    def _get_cheapest_candidates(candidate_list):
        print "Getting cheapest candidates..."
        cheapest_candidates = []
        least_cost = 99999
        for candidate in candidate_list:
            this_cost = candidate.get_total_pp_cost()
            if this_cost < least_cost:
                least_cost = this_cost
                cheapest_candidates = [candidate]
            elif this_cost == least_cost:
                cheapest_candidates.append(candidate)
        print "Least cost is: %.2f. Number of candidates: %d" % (least_cost, len(cheapest_candidates))
        return cheapest_candidates

    @staticmethod
    def _get_furthest_candidate(candidate_list):
        print "Getting candidate that is furthest away from the other shipyards..."
        max_dist = -1
        chosen_candidate = None
        for candidate in candidate_list:
            this_dist = candidate.get_distance_to_yards()
            if this_dist > max_dist:
                max_dist = this_dist
                chosen_candidate = candidate
        if chosen_candidate:
            print "Best candidate is planet %d at system %d with a distance of %d" % (chosen_candidate.pid,
                                                                                  chosen_candidate.sys_id,
                                                                                  chosen_candidate.get_distance_to_yards())
        return chosen_candidate

    @staticmethod
    def _get_shipbuilding_planets(planet_list):
        universe = fo.getUniverse()
        locs = []
        for pid in planet_list:
            planet = universe.getPlanet(pid)
            if not planet:
                continue
            species_name = planet.speciesName
            if not species_name:
                continue
            species = fo.getSpecies(species_name)
            if not species or not species.canProduceShips:
                continue
            locs.append(pid)
        return locs


class BasicShipyardManager(ShipyardManager):
    """Basic Shipyard"""
    name = "BLD_SHIPYARD_BASE"

    def __init__(self):
        self._reset_lists()
        self.unlocked_hulls.extend(["SH_BASIC_SMALL", "SH_BASIC_MEDIUM", "SH_STANDARD", "SH_XENTRONIUM"])
        self.unlocked_parts.extend([part for part in fo.getEmpire().availableShipParts
                                    if part not in AIDependencies.SHIP_PART_BUILDING_REQUIREMENTS])
        ShipyardManager.__init__(self)


class DryDockManager(BasicShipyardManager):
    """Orbital Drydocks"""
    name = "BLD_SHIPYARD_ORBITAL_DRYDOCK"

    def __init__(self):
        self._reset_lists()
        self.unlocked_hulls.extend(["SH_ROBOTIC", "SH_SPATIAL_FLUX"])
        BasicShipyardManager.__init__(self)
        self.prereqs.append(BasicShipyardManager.name)


class NanoRoboticShipyardManager(DryDockManager):
    """Nanorobotic processing unit."""
    name = "BLD_SHIPYARD_CON_NANOROBO"

    def __init__(self):
        self._reset_lists()
        self.unlocked_hulls.extend(["SH_NANOROBOTIC"])
        DryDockManager.__init__(self)
        self.prereqs.append(DryDockManager.name)


class GeoIntegrationFacilityManager(DryDockManager):
    """Geo-integration facility."""
    name = "BLD_SHIPYARD_CON_GEOINT"

    def __init__(self):
        self._reset_lists()
        self.unlocked_hulls.extend(["SH_SELF_GRAVITATING", "SH_TITANIC"])
        DryDockManager.__init__(self)
        self.prereqs.append(DryDockManager.name)


class AdvancedEngineeringBayManager(DryDockManager):
    """Advanced engineering bay."""
    name = "BLD_SHIPYARD_CON_ADV_ENGINE"

    def __init__(self):
        self._reset_lists()
        self.unlocked_hulls.extend(["SH_TRANSSPATIAL"])
        self.unlocked_parts.extend(["FU_TRANSPATIAL_DRIVE"])
        DryDockManager.__init__(self)
        self.prereqs.append(DryDockManager.name)


class AdvancedRoboticShipsManager(BasicShipyardManager):
    """Eventhough technically not an independent building, handles robotic multi-yards."""
    name = "BLD_SHIPYARD_CON_ADV_ENGINE"

    def __init__(self):
        self._reset_lists()
        self.unlocked_hulls.extend(["SH_LOGISTICS_FACILITATOR"])
        BasicShipyardManager.__init__(self)
        self.prereqs.extend([DryDockManager.name, GeoIntegrationFacilityManager.name,
                             NanoRoboticShipyardManager.name])


class OrbitalIncubatorManager(BasicShipyardManager):
    """Orbital Incubator (most basic organic shipyard)"""
    name = "BLD_SHIPYARD_ORG_ORB_INC"

    def __init__(self):
        self._reset_lists()
        self.unlocked_hulls.extend(["SH_ORGANIC", "SH_STATIC_MULTICELLULAR", "SH_SYMBIOTIC"])
        BasicShipyardManager.__init__(self)
        self.prereqs.append(BasicShipyardManager.name)


class CellularGrowthChamberManager(OrbitalIncubatorManager):
    """Cellular Growth Chamber"""
    name = "BLD_SHIPYARD_ORG_CELL_GRO_CHAMB"

    def __init__(self):
        self._reset_lists()
        self.unlocked_hulls.extend(["SH_PROTOPLASMIC", "SH_BIOADAPTIVE"])
        OrbitalIncubatorManager.__init__(self)
        self.prereqs.append(OrbitalIncubatorManager.name)


class XenoCoordinationFacilityManager(OrbitalIncubatorManager):
    """Xeno-Coordination Facility"""
    name = "BLD_SHIPYARD_ORG_XENO_FAC"

    def __init__(self):
        self._reset_lists()
        self.unlocked_hulls.extend(["SH_ENDOMORPHIC", "SH_RAVENOUS"])
        OrbitalIncubatorManager.__init__(self)
        self.prereqs.append(OrbitalIncubatorManager.name)


class AdvancedOrganicShipsManager(OrbitalIncubatorManager):
    """Handles multi-shipyard-dependend organic ships"""
    name = "BLD_SHIPYARD_ORG_XENO_FAC"

    def __init__(self):
        self._reset_lists()
        self.unlocked_hulls.extend(["SH_ENDOSYMBIOTIC", "SH_SENTIENT"])
        OrbitalIncubatorManager.__init__(self)
        self.prereqs.extend([OrbitalIncubatorManager.name, XenoCoordinationFacilityManager.name,
                             CellularGrowthChamberManager.name])


class EnergyCompressionShipyardManager(BasicShipyardManager):
    """Most basic energy shipyard.

    Needs blue/white star or black hole"""
    name = "BLD_SHIPYARD_ENRG_COMP"

    def __init__(self):
        self._reset_lists()
        self.unlocked_hulls.extend(["SH_COMPRESSED_ENERGY", "SH_ENERGY_FRIGATE", "SH_QUANTUM_ENERGY"])
        BasicShipyardManager.__init__(self)
        self.prereqs.append(BasicShipyardManager.name)

    def _possible_locations(self):
        possible_systems = (AIstate.empireStars.get(fo.starType.blackHole, [])
                            + AIstate.empireStars.get(fo.starType.white, [])
                            + AIstate.empireStars.get(fo.starType.blue, []))
        possible_planets = []
        for sys_id in possible_systems:
            possible_planets.append(PlanetUtilsAI.get_empire_planets_in_system(sys_id))
        return self._get_shipbuilding_planets(possible_planets)


class EnergySolarShipyardManager(EnergyCompressionShipyardManager):
    """Most powerful Energy shipyard.

    Needs black holes."""
    name = "BLD_SHIPYARD_ENRG_SOLAR"

    def __init__(self):
        self._reset_lists()
        self.unlocked_hulls.append("SH_SOLAR")
        EnergyCompressionShipyardManager.__init__(self)
        self.prereqs.append(EnergyCompressionShipyardManager.name)

    def _possible_locations(self):
        black_hole_systems = AIstate.empireStars.get(fo.starType.blackHole, [])
        black_hole_planets = []
        for sys_id in black_hole_systems:
            black_hole_planets.extend(PlanetUtilsAI.get_empire_planets_in_system(sys_id))
        return self._get_shipbuilding_planets(black_hole_planets)


class AsteroidShipyardManager(BasicShipyardManager):
    name = "BLD_SHIPYARD_AST"
    shipyard_is_system_wide = True

    def __init__(self):
        self._reset_lists()
        self.unlocked_hulls.extend(["SH_ASTEROID", "SH_SMALL_ASTEROID", "SH_HEAVY_ASTEROID",
                                    "SH_CAMOUFLAGE_ASTEROID", "SH_SMALL_CAMOUFLAGE_ASTEROID"])
        BasicShipyardManager.__init__(self)
        self.prereqs.append(BasicShipyardManager.name)

    def _possible_locations(self):
        locs = []
        for sys_id in get_empire_asteroid_systems():
            locs.extend(PlanetUtilsAI.get_empire_planets_in_system(sys_id))
        return self._get_shipbuilding_planets(locs)


class AsteroidRefinementManager(AsteroidShipyardManager):
    # Locations are same as AsteroidShipyard, cf. that class...
    name = "BLD_SHIPYARD_AST_REF"

    def __init__(self):
        self._reset_lists()
        self.unlocked_hulls.extend([])
        AsteroidShipyardManager.__init__(self)
        self.system_prereqs.append(AsteroidShipyardManager.name)


class ShipyardLocationCandidate(object):
    """."""  # TODO Docstring
    INVALID_DISTANCE = 9999

    def __init__(self, pid, bld_name, prereqs, system_prereqs, rating, improvement, yard_locs, shipyard_is_system_wide):
        universe = fo.getUniverse()
        self.this_shipyard = bld_name
        self.name = bld_name
        self.pid = pid
        self.sys_id = universe.getPlanet(pid).systemID
        self._dist_to_yards = self._calc_minimum_distance_to_yards(yard_locs)
        self.system_prereqs = system_prereqs
        self._missing_prereqs = self._calc_missing_prereqs(prereqs)
        self._total_pp_cost = self._calc_total_pp_cost()
        self.rating = rating
        self.improvement = improvement
        self.shipyard_is_system_wide = shipyard_is_system_wide

    def get_distance_to_yards(self):
        """Return minimum distance to yard of same rating."""
        return self._dist_to_yards

    def get_total_pp_cost(self):
        """Return total pp cost of the building including missing prerequisites."""
        return self._total_pp_cost

    def get_missing_prereqs(self):
        """Return a list of missing prerequisites."""
        return list(self._missing_prereqs)

    def _calc_minimum_distance_to_yards(self, yard_locs):
        universe = fo.getUniverse()
        min_dist = self.INVALID_DISTANCE
        for yard_sys_id in yard_locs:
            this_dist = universe.jumpDistance(self.sys_id, yard_sys_id)
            min_dist = min(min_dist, this_dist)
        return min_dist

    def _calc_missing_prereqs(self, prereqs):
        missing_prereqs = []
        for prereq in prereqs:
            if self.pid not in ProductionAI.bld_cache.existing_buildings.get(prereq, []):
                missing_prereqs.append(prereq)
        if self.system_prereqs:
            planet_ids = PlanetUtilsAI.get_empire_planets_in_system(self.sys_id)
            for prereq in self.system_prereqs:
                existing_locs = ProductionAI.bld_cache.existing_buildings.get(self.this_shipyard, [])
                if not any(pid in existing_locs for pid in planet_ids):
                    missing_prereqs.append(prereq)
        return missing_prereqs

    def _calc_total_pp_cost(self):
        empire_id = fo.empireID()
        total_cost = fo.getBuildingType(self.this_shipyard).productionCost(empire_id, self.pid)
        for prereq in self._missing_prereqs:
            total_cost += fo.getBuildingType(prereq).productionCost(empire_id, self.pid)
        return total_cost


shipyard_map = {manager.name: manager for manager in
                [
                    BasicShipyardManager,
                    DryDockManager,
                    NanoRoboticShipyardManager,
                    GeoIntegrationFacilityManager,
                    AdvancedEngineeringBayManager,
                    AdvancedRoboticShipsManager,
                    OrbitalIncubatorManager,
                    CellularGrowthChamberManager,
                    XenoCoordinationFacilityManager,
                    AdvancedOrganicShipsManager,
                    EnergyCompressionShipyardManager,
                    EnergySolarShipyardManager,
                    AsteroidShipyardManager,
                    AsteroidRefinementManager,
                ]}


def get_empire_asteroid_systems():
    """Return set of empire's asteroid systems."""
    universe = fo.getUniverse()
    asteroid_systems = set()
    for pid in list(AIstate.popCtrIDs) + list(AIstate.outpostIDs):
        planet = universe.getPlanet(pid)
        if planet and planet.size == fo.planetSize.asteroids:
            asteroid_systems.add(planet.systemID)
    return asteroid_systems
