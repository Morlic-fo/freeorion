import freeOrionAIInterface as fo
import pickle
import FreeOrionAI as foAI

def dump_known_universe():
    """Dump information about the known universe for out-of-game calculation of chokepoints etc."""
    systems = fo.getUniverse().systemIDs
    used_relations = set()
    nodes = set()
    universe = fo.getUniverse()
    for system in systems:
        name = universe.getSystem(system).name
        name = name.decode('utf8')
        name = '%s(%s)' % (name, system)
        starlines = foAI.foAIstate.systemStatus[system].get('neighbors', set())
        print starlines
        for rel in starlines:
            edge = frozenset((system, rel))
            if edge in used_relations:
                continue
            else:
                used_relations.add(edge)
        owners = set()
        for pid in universe.getSystem(system).planetIDs:
            planet = universe.getPlanet(pid)
            owners.add(planet.owner)
        tags = set()
        for owner in owners:
            if owner == fo.getEmpire().empireID:
                tags.add('owned')
            elif owner == -1:
                continue
            else:
                tags.add('enemies')
        if system in foAI.foAIstate.unexploredSystemIDs:
            tags.add('unexplored')
        tags = frozenset(tags)
        nodes.add((system, name, (universe.getSystem(system).x,universe.getSystem(system).y), tags))


    known_universe = {
        'nodes': nodes,
        'edges': used_relations,
    }
    output = open('KnownUniverseAI%d.pkl'%fo.getEmpire().empireID, 'wb')
    print "dumping file"
    pickle.dump(known_universe, output)
    output.close()
