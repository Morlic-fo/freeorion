from collections import OrderedDict
import os
import sys
from glob import glob
from ast import literal_eval
import traceback
import pylab

import networkx as nx
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.widgets import CheckButtons
from matplotlib.legend_handler import HandlerPatch

import Tkinter as tk
import ttk
import uuid

class HandlerCircle(HandlerPatch):
    def create_artists(self, legend, orig_handle,
                       xdescent, ydescent, width, height, fontsize, trans):
        center = 0.5 * width - 0.5 * xdescent, 0.5 * height - 0.5 * ydescent
        p = mpatches.Circle(xy=center, radius=0.5*(height - ydescent))
        self.update_prop(p, orig_handle, legend)
        p.set_transform(trans)
        return [p]


class NoNodeFoundException(Exception):
    pass


def j_tree(tree, parent, dic):
    for key in sorted(dic.keys()):
        uid = uuid.uuid4()
        if isinstance(dic[key], dict):
            tree.insert(parent, 'end', uid, text=key)
            j_tree(tree, uid, dic[key])
        elif isinstance(dic[key], tuple):
            tree.insert(parent, 'end', uid, text=str(key) + '()')
            j_tree(tree, uid,
                   dict([(i, x) for i, x in enumerate(dic[key])]))
        elif isinstance(dic[key], list):
            tree.insert(parent, 'end', uid, text=str(key) + '[]')
            j_tree(tree, uid,
                   dict([(i, x) for i, x in enumerate(dic[key])]))
        else:
            value = dic[key]
            if isinstance(value, str):
                value = value.replace(' ', '_')
            tree.insert(parent, 'end', uid, text=key, value=value)


root = None

def tk_tree_view(data):
    # Setup the root UI
    global root
    root = tk.Tk()
    root.title("tk_tree_view")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)

    # Setup the Frames
    tree_frame = ttk.Frame(root, padding="3")
    tree_frame.grid(row=0, column=0, sticky=tk.NSEW)

    # Setup the Tree
    tree = ttk.Treeview(tree_frame, columns=('Values'))
    tree.column('Values', width=100, anchor='center')
    tree.heading('Values', text='Values')
    j_tree(tree, '', data)
    tree.pack(fill=tk.BOTH, expand=1)

    # Limit windows minimum dimensions
    root.update_idletasks()
    root.minsize(root.winfo_reqwidth(), root.winfo_reqheight())
    root.mainloop()


class OnClickCallback:
    def __init__(self, xdata, ydata, aux_data,
                 axis=None, xtol=None, ytol=None):
        self.data = zip(xdata, ydata, aux_data)
        if xtol is None:
            xtol = ((max(xdata) - min(xdata))/float(len(xdata)))/2
        if ytol is None:
            ytol = ((max(ydata) - min(ydata))/float(len(ydata)))/2
        self.xtol = xtol
        self.ytol = ytol
        if axis is None:
            axis = pylab.gca()
        self.axis = axis
        print xdata
        print ydata

    def __call__(self, event):
        print "Callback"
        # only react to clicks in our axis
        if not event.inaxes or self.axis != event.inaxes:
            print "Not in axes..."
            return

        try:
            x, y, aux = self.find_nearest_node(event.xdata, event.ydata)
        except NoNodeFoundException:
            print "No Node found!"
            return

        self.display_data(aux)

    def find_nearest_node(self, x_click, y_click):
        print x_click, y_click
        print self.data
        candidates = [
            ((x - x_click) ** 2 + (y - y_click) ** 2, x, y, aux)
            for x, y, aux in self.data if
            x_click - self.xtol < x < x_click + self.xtol and
            y_click - self.ytol < y < y_click + self.ytol
        ]
        if not candidates:
            raise NoNodeFoundException()

        candidates.sort()
        distance, x, y, aux = candidates[0]
        return x, y, aux

    def display_data(self, data):
        # do something with the annote value
        global root
        if root is not None:
            root.destroy()
        tk_tree_view(data)


def parse_file(file_name):
    print "processing file ", file_name
    sys.stdout.flush()
    nodes = []
    edges = []
    empire_id = -2
    empire_name = ''
    with open(unicode(file_name, 'utf-8'), 'r') as lf:
        while True:
            line = lf.readline()
            if not line:
                break

            if not empire_name and "EmpireID:" in line and "Name:" in line:
                empire_name = line.split("Name:")[1].split("Turn:")[0].strip()

            # we are only interested in newest graph
            if "Dumping Universe Graph" in line:
                del nodes[:]
                del edges[:]
                empire_id = literal_eval(line.split("EmpireID:")[1].strip())
                print "Found dumped universe graph"

            if "__N__" in line:
                try:
                    node_tuple = eval(line.split("__N__")[1].strip())
                except:
                    print line.split("__N__")[1].strip()
                    raise

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
    return g, empire_id, empire_name


color_map = OrderedDict([('Home', '#00008B'),
                         ('Own Colony', '#4169E1'),
                         ('Own Border Colony', '#3ADF00'),
                         ('Unowned Border System', '#FFFF00'),
                         ('Expansion System', '#F2F5A9'),
                         ('Misc', '#B0B0B0'),
                         ('Scanned', '#808080'),
                         ('Unexplored', '#000000'),
                         ('Offensive System', '#DC143C'),
                         ('Other Enemy System', '#F78181'),
                         ('Inner System', '#FFFFFF')
                         ])
color_name_lookup = OrderedDict([(tag, color) for color, tag in color_map.items()])


def _extract_borders(g):
    all_borders = {data['border_number'] for n, data in g.nodes(data=True) if 'border_number' in data}
    border_map = {
        i: (
            [n for n, data in g.nodes(data=True) if data.get('border_number') == i],
            next(data.get('threat_sources') for n, data in g.nodes(data=True) if data.get('border_number') == i)
        ) for i in all_borders
    }
    return border_map


def draw(g, empire_id, empire_name):
    fig = plt.figure()
    plot_selection = {'Border systems': True, 'Expansion systems': False, 'Offensive Systems': False}
    border_map = _extract_borders(g)
    edges = [(u, v) for (u, v) in g.edges()]
    pos = {n: (data['pos'][0], -data['pos'][1]) for n, data in g.nodes(data=True)}  # positions for all nodes

    ax_graph = plt.axes()

    def get_color(data_dict):
        if data_dict.get('home_system', False):
            return color_map['Home']

        if data_dict.get('border_system', False) and plot_selection['Border systems']:
            if empire_id in data_dict.get('owners', []):
                return color_map['Own Border Colony']
            else:
                return color_map['Unowned Border System']

        if data_dict.get('offensive_system', False) and plot_selection['Offensive Systems']:
            print plot_selection
            return color_map['Offensive System']

        if data_dict.get('owners', []):
            if empire_id in data_dict.get('owners', []):
                return color_map['Own Colony']
            else:
                return color_map['Other Enemy System']

        if data_dict.get('expansion_system', False) and plot_selection['Expansion systems']:
            return color_map['Expansion System']

        if not data_dict.get('explored', False):
            if data_dict.get('scanned', False):
                return color_map['Scanned']
            else:
                return color_map['Unexplored']

        if data_dict.get('inner_system', False):
            return color_map['Inner System']

        return color_map['Misc']

    def draw_graph():
        plt.sca(ax_graph)
        ax_graph.clear()
        print plot_selection
        node_colors = [get_color(data) for n, data in g.nodes(data=True)]
        print node_colors
        nx.draw_networkx_nodes(g, pos, ax=ax_graph, node_size=100, node_color=node_colors)
        colors_present = [_c for _c in color_name_lookup if _c in node_colors]
        legend_symbols = [mpatches.Circle((1, 1), 1, facecolor=_c, edgecolor="black") for _c in colors_present]
        legend_labels = [color_name_lookup[_c] for _c in colors_present]
        plt.legend(legend_symbols, legend_labels, handler_map={mpatches.Circle: HandlerCircle()})
        nx.draw_networkx_edges(g, pos, ax=ax_graph, edgelist=edges, width=1, alpha=0.5, edge_color='b', style='dashed')
        pos_labels = {n: (x, y - 15) for n, (x, y) in pos.items()}
        labels = {n: unicode(data['name'], 'utf-8') for n, data in g.nodes(data=True)}
        for i, (border_systems, threat_sources) in border_map.iteritems():
            for n in set(border_systems) | set(threat_sources):
                labels[n] += labels[n] + ' B%d' % i
        nx.draw_networkx_labels(g, pos_labels, ax=ax_graph, font_size=10, font_family='DejaVu Sans', labels=labels)
        plt.axis('off')
        try:
            name_parts = empire_name.split('_')
            ai_num = name_parts[4].split('RIdx')[0]
            short_name = name_parts[0] + '_' + name_parts[3] + '_' + ai_num
        except:
            short_name = "AI Empire ID: %d" % empire_id
        plt.title(short_name)

        all_x = []
        all_y = []
        all_data = []
        for n, data in g.nodes(data=True):
            all_x.append(data['pos'][0])
            all_y.append(-data['pos'][1])
            all_data.append(data)
        fig = plt.gcf()
        fig.canvas.mpl_connect('button_press_event', OnClickCallback(all_x, all_y, all_data))
        fig.canvas.draw()


    draw_graph()

    def selection_changed_callback_fcn(label):
        plot_selection[label] = not plot_selection[label]
        draw_graph()

    # add checkboxes to limit the shown data
    rax = plt.axes([0.02, 0.4, 0.2, 0.15])
    selection_checkboxes = CheckButtons(
            rax, ('Border systems', 'Expansion systems', 'Offensive Systems'),
            (
                plot_selection['Border systems'], plot_selection['Expansion systems'],
                plot_selection['Offensive Systems']))

    selection_checkboxes.on_clicked(selection_changed_callback_fcn)

    mng = plt.get_current_fig_manager()

    try:
        backend = plt.get_backend()
        print "Detected backend:", backend
        if backend == 'TkAgg':
            mng.window.state('zoomed')
        elif backend == 'wxAgg':
            mng.frame.Maximize(True)
        elif backend == 'QT4Agg':
            mng.window.showMaximized()
        else:
            raise Exception('Unsupported Backend')
    except:
        mng.resize(1200, 1000)
    # plt.savefig("universe.png")  # save as png
    plt.show(block=True)


def main():
    if os.name == 'nt':
        home = os.path.expanduser("~")
        data_dir = home + "\\AppData\\Roaming\\FreeOrion"
    elif os.name == 'posix':
        data_dir = (os.environ.get('XDG_DATA_HOME', os.environ.get('HOME', "") + "/.local/share") + "/freeorion")
    else:
        data_dir = (os.environ.get('HOME', "") + "/.freeorion")

    logfiles = sorted(glob(data_dir + os.sep + "A*.log"))
    log_ai1 = glob(data_dir + os.sep + "AI_1.log")
    if log_ai1 and log_ai1[0] in logfiles:
        time_ai1 = os.path.getmtime(log_ai1[0])
        for path in logfiles[::-1]:
            logtime = os.path.getmtime(path)
            if logtime < time_ai1 - 300:
                del logfiles[logfiles.index(path)]
                print "skipping stale logfile ", path
    for lfile in logfiles:
        try:
            g, empire_id, empire_name = parse_file(lfile)
            if len(g.nodes()) > 1:
                draw(g, empire_id, empire_name)
        except:
            print >> sys.stderr, "Couldn't parse file:"
            print >> sys.stderr, traceback.format_exc()

if __name__ == "__main__":
    main()
