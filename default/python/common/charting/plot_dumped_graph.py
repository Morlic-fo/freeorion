import os
import sys
from glob import glob
from ast import literal_eval
import traceback

import networkx as nx
import matplotlib.pyplot as plt

def parse_file(file_name):
    print "processing file ", file_name
    sys.stdout.flush()
    nodes = []
    edges = []
    empire_id = -2
    with open(unicode(file_name, 'utf-8'), 'r') as lf:
        while True:
            line = lf.readline()
            if not line:
                break

            # we are only interested in newest graph
            if "Dumping Universe Graph" in line:
                del nodes[:]
                del edges[:]
                empire_id = literal_eval(line.split("EmpireID:")[1].strip())
                print "Found dumped universe graph"

            if "__N__" in line:
                node_tuple = literal_eval(line.split("__N__")[1].strip())
                nodes.append(node_tuple)

            elif "__E__" in line:
                edges.append(literal_eval(line.split("__E__")[1].strip()))

    g = nx.Graph()
    for node in nodes:
        print node
    g.add_nodes_from(nodes)
    for edge in edges:
        print edge
    g.add_edges_from(edges)
    return g, empire_id


def draw(G, empire_id):
    edges = [(u, v) for (u, v) in G.edges()]

    pos = {n: (data['pos'][0], -data['pos'][1]) for n, data in G.nodes(data=True)}  # positions for all nodes

    def get_color(data_dict):
        if data_dict.get('home_system', False):
            return '#00008B'
        if not data_dict.get('explored', False):
            return '#808080'
        elif not data_dict.get('owners', []):
            if data_dict.get('border_system', False):
                return '#FFFF00'
            elif data_dict.get('expansion_system', False):
                return '#F2F5A9'
            else:
                return '#000000'
        elif empire_id in data_dict.get('owners', []):
            if data_dict.get('border_system', False):
                return '#3ADF00'
            else:
                return '#4169E1'
        else:
            if data_dict.get('offensive_system', False):
                return '#DC143C'
            else:
                return '#F78181'

    nx.draw_networkx_nodes(G, pos, node_size=100,
                           node_color=[get_color(data) for n, data in G.nodes(data=True)],
                           )
    # edges
    nx.draw_networkx_edges(G, pos, edgelist=edges, width=1, alpha=0.5, edge_color='b', style='dashed')
    # labels
    pos = {k: (a, b - 15) for k, (a, b) in pos.items()}
    nx.draw_networkx_labels(G, pos, font_size=10, font_family='DejaVu Sans', labels={n: unicode(data['name'], 'utf-8') for n, data in G.nodes(data=True)},
                            )
    plt.axis('off')
    mng = plt.get_current_fig_manager()
    mng.resize(1200,1000)
    # plt.savefig("universe.png")  # save as png
    plt.show(block=True)


def main():
    if os.name == 'nt':
        home = os.path.expanduser("~")
        dataDir = home + "\\AppData\\Roaming\\FreeOrion"
    else:
        dataDir = (os.environ.get('HOME', "") + "/.freeorion") if os.name != 'posix' else (os.environ.get('XDG_DATA_HOME', os.environ.get('HOME', "") + "/.local/share") + "/freeorion")
    
    print "Starting script"
    logfiles = sorted(glob(dataDir + os.sep + "A*.log"))
    A1log = glob(dataDir + os.sep + "AI_1.log")
    if A1log and A1log[0] in logfiles:
        A1Time = os.path.getmtime(A1log[0])
        for path in logfiles[::-1]:
            logtime = os.path.getmtime(path)
            if logtime < A1Time - 300:
                del logfiles[logfiles.index(path)]
                print "skipping stale logfile ", path
    for lfile in logfiles:
        try:
            g, empire_id = parse_file(lfile)
            if len(g.nodes()) > 1:
                draw(g, empire_id)
        except Exception as e:
            print >> sys.stderr, "Couldn't parse file:"
            print >> sys.stderr, traceback.format_exc()

if __name__ == "__main__":
    main()
