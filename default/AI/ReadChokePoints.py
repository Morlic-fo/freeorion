import pickle
import operator
import matplotlib.pyplot as plt
import networkx as nx
import cProfile, pstats, StringIO
pr = cProfile.Profile()
pr.enable()


def define_graph():
    g = nx.Graph()
    for n, name, pos, tags in data['nodes']:
            color = "#000000"
            if 'enemies' in tags:
                color = '#FF0000'
                enemy_nodes.add(n)
                if 'owned' in tags:
                    color = "#FFFF00"
                    owned_nodes.add(n)
            elif 'owned' in tags:
                color = "#00FF00"
                owned_nodes.add(n)
            if 'unexplored' in tags:
                color = "#CCCCCC"
                unexplored_nodes.add(n)
            if n in all_defensive_positions:
                color = "#0000FF"
                if 'owned' in tags:
                    color = "#00FFFF"
                elif 'enemy' in tags:
                    color = "#FF00FF"
            # g.add_node(n, name='%s: %d' % (name, node_values.get(n, 0)), position=pos, color=color)
            g.add_node(n, name='', position=pos, color=color)
    g.add_edges_from(data['edges'])
    return g


def find_defensive_positions(G):
    used_nodes = set()
    node_values.clear()
    shortest_paths = nx.all_pairs_shortest_path(G)
    for source in shortest_paths:
        used_nodes.add(source)
        for target in shortest_paths[source]:
            if target in used_nodes:
                continue
            this_path = shortest_paths[source][target]
            if not any(node in owned_nodes for node in this_path):
                weight = 1e-12  # assign a small weight here to prefer chokepoints
            else:
                weight = 0
                if any(node in enemy_nodes for node in this_path):
                    weight += 20
                if any(node in unexplored_nodes for node in this_path):
                    weight += 10
            idx_owned = [index for index, node in enumerate(this_path) if node in owned_nodes]
            idx_threat = [index for index, node in enumerate(this_path) if node in enemy_nodes or node in unexplored_nodes]
            for this_index, node in enumerate(this_path):
                distance_to_owned = min(abs(this_index - idx) for idx in idx_owned) if idx_owned else 0
                distance_to_threat = min(abs(this_index - idx) for idx in idx_threat) if idx_threat else 0
                aggression_factor = 1  # a measure on how "aggressive" (how close to enemy systems) the found points should be
                distance_factor = max(distance_to_owned, aggression_factor*distance_to_threat, 1)
                node_values[node] = node_values.get(node, 0) + weight / distance_factor
    for node in node_values:
        if node in unexplored_nodes:
            node_values[node] = 0
        else:
            if node in owned_nodes:
                node_values[node] *= 1.2  # prefer owned systems as planets provide additional defensive resources
        if node in all_defensive_positions:
            node_values[node] = 0  # count each node only once.
    maxval = max(node_values.iteritems(), key=operator.itemgetter(1))[1]
    return True if maxval > 1 else False  # to end while loop if all threats are separated from our planets


def draw(G):
    edges = [(u, v) for (u, v) in G.edges()]
    pos = {n: data['position'] for n, data in G.nodes(data=True)}  # positions for all nodes
    nx.draw_networkx_nodes(G, pos, node_size=100,
                           node_color=[data['color'] for n, data in G.nodes(data=True)],
                           )
    # edges
    nx.draw_networkx_edges(G, pos, edgelist=edges, width=1, alpha=0.5, edge_color='b', style='dashed')
    # labels
    pos = {k: (a, b - 15) for k, (a, b) in pos.items()}
    nx.draw_networkx_labels(G, pos, font_size=10, font_family='Arial', labels={n: data['name'] for n, data in G.nodes(data=True)})
    plt.axis('off')
    # plt.savefig("universe.png")  # save as png
    plt.show(block=True)


# read file
empireID = 2
filename = r'C:\Program Files (x86)\FreeOrion\ KnownUniverseAI%d.pkl' % empireID
pkl_file = open(filename, 'rb')
data = pickle.load(pkl_file)
pkl_file.close()
# define graph and init used data structs
node_values = {}
all_defensive_positions = set()
owned_nodes = set()
enemy_nodes = set()
unexplored_nodes = set()
G = define_graph()

# find all_defensive_positions
max_tries = 10
cur_try = 1
while find_defensive_positions(G) and cur_try <= max_tries:
    defensive_position = max(node_values.iteritems(), key=operator.itemgetter(1))[0]  # highest rated system
    all_defensive_positions.add(defensive_position)
    G.remove_node(defensive_position)
    cur_try += 1

# print profiling output and plot the graph
G = define_graph()
pr.disable()
s = StringIO.StringIO()
# sortby = 'cumulative'
sortby = 'tot'
ps = pstats.Stats(pr, stream=s).sort_stats(sortby)
ps.print_stats()
print s.getvalue()
draw(G)
