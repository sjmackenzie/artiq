from migen import *


# Based on: https://github.com/Bekbolatov/SortingNetworks/blob/master/src/main/js/gr.js
def boms_get_partner(n, l, p):
    if p == 1:
        return n ^ (1 << (l - 1))
    scale = 1 << (l - p)
    box = 1 << p
    sn = n//scale - n//scale//box*box
    if sn == 0 or sn == (box - 1):
        return n
    if (sn % 2) == 0:
        return n - scale
    return n + scale


def boms_steps_pairs(node_count):
    d = log2_int(node_count)
    steps = []
    for l in range(1, d+1):
        for p in range(1, l+1):
            pairs = []
            for n in range(2**d):
                partner = boms_get_partner(n, l, p)
                if partner != n:
                    if partner > n:
                        pair = (n, partner)
                    else:
                        pair = (partner, n)
                    if pair not in pairs:
                        pairs.append(pair)
            steps.append(pairs)
    return steps


layout_rtio_payload = [
    ("channel", 24),
    ("timestamp", 64),
    ("address", 16),
    ("data", 512),
]


def layout_node_data(seqn_size):
    return [
        ("valid", 1),
        ("seqn", seqn_size),
        ("replace_occured", 1),
        ("payload", layout_rtio_payload)
    ]


def cmp_wrap(a, b):
    return Mux(a[-2:] == ~b[-2:], a[0], a[:-2] < b[:-2])


class OutputNetwork(Module):
    def __init__(self, node_count, seqn_size):
        self.input = [Record(layout_node_data(seqn_size)) for _ in node_count]
        self.output = None

        step_input = self.input
        for step in boms_steps_pairs(node_count):
            step_output = [Record(layout_node_data(seqn_size)) for _ in node_count]

            for node1, node2 in step:
                self.sync += [
                    If(step_input[node1].payload.channel == step_input[node2].payload.channel,
                        If(cmp_wrap(step_input[node1].seqn, step_input[node2].seqn),
                            step_output[node1].eq(step_output[node2]),
                        ).Else(
                            step_output[node1].eq(step_output[node1]),
                        ),
                        step_output[node1].replace_occured.eq(1),
                        step_output[node2].eq(step_input[node2]),
                        step_output[node2].valid.eq(0)
                    ).Elif(step_input[node1].payload.channel < step_input[node2].payload.channel,
                        step_output[node1].eq(step_input[node1]),
                        step_output[node2].eq(step_input[node2])
                    ).Else(
                        step_output[node1].eq(step_input[node2]),
                        step_output[node2].eq(step_input[node1])
                    )
                ]

            unchanged = list(range(node_count))
            for node1, node2 in step:
                unchanged.remove(node1)
                unchanged.remove(node2)
            for node in unchanged:
                self.sync += step_output[node].eq(step_input[node])

            self.output = step_output
            step_input = step_output
