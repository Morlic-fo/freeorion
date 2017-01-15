import freeOrionAIInterface as fo  # pylint: disable=import-error
import AIstate
import PlanetUtilsAI
from EnumsAI import EmpireProductionTypes
from freeorion_tools import print_error
from collections import namedtuple
from sys import stderr
import bisect  # for implementing the ordered list.


BUILDING = EmpireProductionTypes.BT_BUILDING
SHIP = EmpireProductionTypes.BT_SHIP


class ProductionPriority(object):
    # lower number means higher priority
    emergency_factor = 1e-9
    default = 100
    building_low = 1000
    building_base = 100
    building_high = 1
    ship_scout = 100
    ship_orbital_defense = 90
    ship_mil = 80
    ship_outpost = 70
    ship_colo = 60
    ship_troops = 50
    ship_orbital_outpost = 40
    ship_orbital_colo = 30
    ship_orbital_troops = 20
    invalid = 1e10


ProductionQueueElement = namedtuple('ProductionQueueElement', ['current_priority', 'base_priority',
                                                               'item_type', 'item', 'location'])


class ProductionQueueManager(object):
    """This class handles the priority management of the production queue.

    This class should be instanced only once and only by foAI.foAIstate in order to provide save-load functionality!

    It is absolutely mandatory that any enqueuing and dequeuing regarding the production queue
    is handled by this class. NEVER call the following functions directly:
        -fo.issueEnqueueBuildingProductionOrder
        -fo.issueEnqueueShipProductionOrder
        -fo.issueRequeueProductionOrder
        -fo.issueDequeueProductionOrder
    Instead, use the dedicated member functions of this class.

    If extending the interface, make sure to always update self._production_queue. Make sure that its content
    is consistent with the ingame production queue (i.e. C++ part of the game) at all times - both item and order.
    """

    def __init__(self):
        self._production_queue = []
        self._number_of_invalid_priorities = 0  # number of items that were assigned an invalid priority
        self._last_update = -1                  # turn in which the queue was last updated

    def __getstate__(self):
        return tuple(self._production_queue), self._number_of_invalid_priorities

    def __setstate__(self, state):
        self.__init__()
        self._production_queue = list(state[0])
        self._number_of_invalid_priorities = state[1]
        self._last_update = -1

    def __len__(self):
        return len(self._production_queue)

    def __repr__(self):
        return 'ProductionQueueManager({})'.format(self._production_queue)

    def update_for_new_turn(self):
        """Check for completed items, adjust priorities to production progress and reorder the queue accordingly.

        This function needs to be called once at the beginning of each turn before surves_universe() is called.
        """
        cur_turn = fo.currentTurn()
        if self._last_update == cur_turn:
            return
        self._last_update = cur_turn

        self._resolve_production_queue_diff()
        self._update_production_priorities()

    def _update_production_priorities(self):
        """Update production priorites based on building progress and resort the queue accordingly."""
        old_queue = list(self._production_queue)  # copy to loop over while we modify the other list
        for tup in old_queue:
            # We want to complete started / nearly finished projects first thus scale it with ratio of progress.
            idx = bisect.bisect_left(self._production_queue, tup)  # as we sort self._production_queue, need to search!
            try:
                (old_priority, base_priority, item_type, this_item, loc) = self._production_queue.pop(idx)
            except ValueError as e:
                print self._production_queue
                print idx
                print self._production_queue[idx]
                print_error(e)
                raise e
            ingame_production_queue = fo.getEmpire().productionQueue  # make sure list represents latest changes
            element = ingame_production_queue[idx]
            total_cost, total_turns = fo.getEmpire().productionCostAndTime(element)
            new_priority = base_priority * (1 - float(element.progress) / float(total_cost))
            new_entry = ProductionQueueElement(new_priority, base_priority, item_type, this_item, loc)
            new_index = bisect.bisect_left(self._production_queue, new_entry)
            self._production_queue.insert(new_index, new_entry)
            if new_index != idx:  # need to move item
                fo.issueRequeueProductionOrder(idx, new_index)
        print "New AI-priority-queue:  # (cur_priority, base_priority, type(1=BUILDING, 2=SHIP), item, loc)"
        print self._production_queue

    def _resolve_production_queue_diff(self):
        """Resolve any diff between the ingame production queue and our stored queue."""
        # Loop over all elements in the ingame_production_queue and try to find a match in self._production_queue.
        # As order is preserved between turns, if items do not match, the corresponding item in self._production_queue
        # must have been completed last turn. In that case, remove the entry from our list.
        ingame_production_queue = fo.getEmpire().productionQueue
        print "Checking Production queues:"
        print "AI-priority-queue from last turn:  # (cur_priority, base_priority, type(1=BUILDING, 2=SHIP), item, loc)"
        print self._production_queue

        ingame_queue_list = []
        for element in ingame_production_queue:
            ingame_queue_list.append(self.get_name_of_production_queue_element(element))
        print "Ingame Production queue this turn: ", ingame_queue_list
        lost_planets, gained_planets = get_planet_diff_since_last_turn()
        items_finished_last_turn = []
        items_lost_last_turn = []
        conquered_items_last_turn = []
        for i, element in enumerate(ingame_production_queue):
            item = self.get_name_of_production_queue_element(element)

            # if the production queue element is located on one of the planets we conquered this turn,
            # then we conquered some project started by an enemy. For now, we do not want to complete it.
            # Instead, we move it to the end of the production queue and mark it as invalid.
            if element.locationID in gained_planets:
                cur_priority = ProductionPriority.invalid + self._number_of_invalid_priorities
                base_priority = ProductionPriority.invalid + self._number_of_invalid_priorities
                self._number_of_invalid_priorities += 1
                item_type = BUILDING if element.buildType == BUILDING else SHIP
                loc = element.locationID
                self._production_queue.insert(i, ProductionQueueElement(cur_priority, base_priority,
                                                                        item_type, item, loc))
                conquered_items_last_turn.append(ProductionQueueElement(cur_priority, base_priority,
                                                                        item_type, item, loc))
                continue  # queue will be sorted later on TODO: maybe use the item, could also dequeue...

            # If the production queue element is not located on a newly conquered planet, then we should have a record
            # of the item in our queue. If any other item in our queue is at this index, then that item was completed
            # last turn or the planet it was built on was conqured by an enemy. In both cases, we can remove the item
            # from our queue and mark it accordingly for later reference. We repeat checking items in our local queue
            # until we find a match with the ingame production queue element.
            while True:
                try:
                    (cur_priority, base_priority, item_type, this_item, loc) = self._production_queue[i]
                    if this_item == item and loc == element.locationID:  # item not finished yet, keep in list
                        break
                    elif loc in lost_planets:
                        items_lost_last_turn.append(self._production_queue.pop(i))
                    else:
                        items_finished_last_turn.append(self._production_queue.pop(i))
                except Exception as e:
                    print >> stderr, "Error when trying to find the %dth element of the queue" % i
                    print >> stderr, "Queue item %s at planet %d" % (element.name, element.locationID)
                    print >> stderr, "Items we currently consider finished last turn: ", items_finished_last_turn
                    print >> stderr, "Current entries of priority_queue:", self._production_queue
                    print_error(e)
                    break

        # After looping through all the items in the ingame production queue,
        # we may still have some items in our local queue left. Those must have
        # either been finished last turn or conquered by an enemy.
        print "New production_queue before cleaning up remaining items: ", self._production_queue
        for remaining_item in self._production_queue[len(ingame_production_queue):]:
            (cur_priority, base_priority, item_type, this_item, loc) = remaining_item
            if loc in lost_planets:
                items_lost_last_turn.append(remaining_item)  # cant pop yet as we loop over the list
            else:
                items_finished_last_turn.append(remaining_item)
        del self._production_queue[len(ingame_production_queue):]
        print "Production_queue after cleaning up remeining items: ", self._production_queue

        print "Items that were finished in last turn: ", items_finished_last_turn
        print "Items that we were building on planets we lost during last turn: ", items_lost_last_turn
        print "Items that were already queued on planets we conquered last turn: ", conquered_items_last_turn

    @staticmethod
    def get_name_of_production_queue_element(elem):
        """Get the name of an element as used in this class, i.e. the name of a building or the id of a shipdesign.

        :param elem: element of production queue
        :return: name of Building or id of ShipDesign of the element
        """
        return elem.designID if elem.buildType == SHIP else elem.name

    def enqueue_item(self, item_type, item, loc, priority=ProductionPriority.default, print_enqueue_errors=True):
        """Enqueue item into production queue.

        :param item_type: type of the item to queue: ship or building
        :type item_type: EnumsAI.AIEmpireProductionTypes
        :param item: Building name or ShipDesign id
        :type item: str or int
        :param priority: production priority
        :type priority: float
        :param loc: Planet id
        :type loc: int
        :param print_enqueue_errors: Flag to turn error logging off. Used when trying locations and errors are expected.
        :type print_enqueue_errors: bool
        :return: True if successfully enqueued, otherwise False
        :rtype: bool
        """
        print "Trying to enqueue %s at %d" % (item, loc)
        if item_type == BUILDING:
            production_order = fo.issueEnqueueBuildingProductionOrder
        elif item_type == SHIP:
            production_order = fo.issueEnqueueShipProductionOrder
        else:
            print_error("Tried to queue invalid item to production queue.",
                        location="ProductionQueueManager.enqueue_item(%s, %s, %s, %s)" % (
                            priority, item_type, item, loc),
                        trace=False)
            return False

        # issue production order to server
        try:
            res = production_order(item, loc)
        except Exception as e:
            print_error(e)
            return False
        if not res:
            if print_enqueue_errors:
                print_error("Can not queue item to production queue.",
                            location="ProductionQueueManager.enqueue_item(%s, %s, %s, %s)" % (
                                priority, item_type, item, loc),
                            trace=False)
            return False

        # Reaching here means we successfully enqueued the item, so let's keep track of it.
        entry = ProductionQueueElement(priority, priority, item_type, item, loc)
        idx = bisect.bisect(self._production_queue, entry)
        self._production_queue.insert(idx, entry)
        if idx == len(self._production_queue) - 1:  # item does not need to be moved in queue
            print "After enqueuing ", item, ":"
            print "self._production_queue: ", [tup[3] for tup in self._production_queue]
            print "real productionQueue: ", [self.get_name_of_production_queue_element(elem)
                                             for elem in fo.getEmpire().productionQueue]
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
        try:
            print "self._production_queue: ", [tup[3] for tup in self._production_queue]
        except IndexError as e:
            print "INDEXERROR CAUGHT: self._production_queue:"
            print self._production_queue
            print_error(e)
            raise e
        print "real productionQueue: ", [self.get_name_of_production_queue_element(elem)
                                         for elem in fo.getEmpire().productionQueue]
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
        new_priority = ProductionPriority.invalid + self._number_of_invalid_priorities
        new_entry = ProductionQueueElement(*[new_priority, new_priority, item_tuple[2:]])
        self._number_of_invalid_priorities += 1
        self._production_queue.append(new_entry)

    def get_all_queued_buildings(self):
        """Get all building orders that are currently queued.

        :return: map from building name to planet ids
        :rtype: dict[str: list[int]]
        """
        queued_bldgs = {}
        for element in self._production_queue:
            if element.item_type == BUILDING:
                queued_bldgs.setdefault(element.item, []).append(element.location)
        return queued_bldgs


def get_planet_diff_since_last_turn():
    """Find the planets that were lost or gained during last turn by comparing with last known universe state.

    :return: lost planets, gained planets
    :rtype: tuple[set, set]
    """
    all_planets = fo.getUniverse().planetIDs
    currently_owned_planets = set(PlanetUtilsAI.get_owned_planets_by_empire(all_planets))
    old_outposts = AIstate.outpostIDs
    old_popctrs = AIstate.popCtrIDs
    old_owned_planets = set(old_outposts + old_popctrs)
    newly_gained_planets = currently_owned_planets - old_owned_planets
    lost_planets = old_owned_planets - currently_owned_planets
    return lost_planets, newly_gained_planets
